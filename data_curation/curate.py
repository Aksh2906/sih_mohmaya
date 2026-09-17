#!/usr/bin/env python3
"""Standalone, offline screen-dataset curation. See README.md for the input contract."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageStat
except ImportError:
    raise SystemExit("Pillow is required. Run: python -m pip install 'Pillow>=12,<13'")

VARIANTS = ("theme", "scale", "crop", "occlusion", "pii", "cursor", "overlay")
SENSITIVITIES = {"sensitive", "non_sensitive", "unknown"}
CLASSES = {"button", "input", "text", "image", "link", "checkbox", "container", "unknown"}
ROLES = {
    "button": "button",
    "submit": "button",
    "edittext": "input",
    "textbox": "input",
    "input": "input",
    "textarea": "input",
    "textview": "text",
    "text": "text",
    "imageview": "image",
    "image": "image",
    "img": "image",
    "link": "link",
    "a": "link",
    "checkbox": "checkbox",
    "container": "container",
    "viewgroup": "container",
}
PII_PATTERNS = (
    ("email", re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")),
    ("credential", re.compile(r"\b(?:password|api[_ -]?key|secret|access[_ -]?token)\b", re.I)),
    ("phone_candidate", re.compile(r"(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)")),
)


def dump_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def digest_image(image):
    return hashlib.sha256(f"{image.size}:RGB:".encode() + image.tobytes()).hexdigest()


def rng_for(seed, identity):
    return random.Random(hashlib.sha256(f"{seed}:{identity}".encode()).digest())


def split_for(group, seed):
    value = rng_for(seed, "split:" + group).random()
    return "train" if value < 0.8 else "validation" if value < 0.9 else "test"


def validate_box(box, width, height):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError("bbox must contain four xyxy coordinates")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
        raise ValueError("bbox coordinates must be finite numbers")
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError("bbox is empty, reversed, or outside the image")


def pixel_box(box):
    # Exclusive right/bottom edges, conservatively covering fractional coordinates.
    return (math.floor(box[0]), math.floor(box[1]), math.ceil(box[2]), math.ceil(box[3]))


def paint_box(image, box, fill):
    left, top, right, bottom = pixel_box(box)
    ImageDraw.Draw(image).rectangle((left, top, right - 1, bottom - 1), fill=fill)


def classify(raw):
    if "category" in raw:
        category = raw["category"]
        if category not in CLASSES:
            raise ValueError(f"category must be one of {sorted(CLASSES)}")
        return category, "provided"
    role = str(raw.get("role", "")).lower().split(".")[-1]
    category = ROLES.get(role, "unknown")
    return category, "role_rule" if category != "unknown" else "unreviewed"


def sensitivity(raw):
    if "sensitivity" in raw:
        tag = raw["sensitivity"]
        if tag not in SENSITIVITIES:
            raise ValueError(f"sensitivity must be one of {sorted(SENSITIVITIES)}")
        return tag, "provided", str(raw.get("pii_type", "unspecified")) if tag == "sensitive" else None
    for kind, pattern in PII_PATTERNS:
        if pattern.search(str(raw.get("text", ""))):
            return "sensitive", "text_rule_candidate", kind
    return "unknown", "unreviewed", None


def normalize_elements(record, size):
    width, height = size
    units = record.get("bbox_units", "pixels")
    if units not in {"pixels", "normalized"}:
        raise ValueError("bbox_units must be pixels or normalized")
    elements = record.get("elements", [])
    if not isinstance(elements, list):
        raise ValueError("elements must be a list")
    result, ids = [], set()
    for index, raw in enumerate(elements):
        if not isinstance(raw, dict):
            raise ValueError("each element must be an object")
        box = raw.get("bbox")
        validate_box(box, 1 if units == "normalized" else width, 1 if units == "normalized" else height)
        box = [float(v) for v in box]
        if units == "normalized":
            box = [box[0] * width, box[1] * height, box[2] * width, box[3] * height]
        element_id = str(raw.get("id", f"element-{index}"))
        if not element_id or element_id in ids:
            raise ValueError("element ids must be nonempty and unique per screenshot")
        ids.add(element_id)
        category, category_method = classify(raw)
        tag, method, pii_type = sensitivity(raw)
        result.append(
            {
                "id": element_id,
                "bbox": box,
                "category": category,
                "category_method": category_method,
                "sensitivity": tag,
                "sensitivity_method": method,
                "pii_type": pii_type,
                "retained_area_fraction": 1.0,
                "visible_fraction": 1.0,
            }
        )
    return result


def read_records(manifest):
    with manifest.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("record must be an object")
            except (ValueError, TypeError) as exc:
                raise ValueError(f"manifest line {line_number}: invalid JSON object") from exc
            yield line_number, record


def load_record(record, manifest):
    image_path = record.get("image")
    if not isinstance(image_path, str) or not image_path:
        raise ValueError("image must be a local file path")
    path = Path(image_path).expanduser()
    if not path.is_absolute():
        path = manifest.parent / path
    with Image.open(path) as opened:
        if opened.getexif().get(274, 1) != 1:
            raise ValueError("normalize EXIF orientation and annotation coordinates before importing")
        image = opened.convert("RGB")
    if min(image.size) < 64:
        raise ValueError("screenshots must be at least 64 x 64 pixels")
    source = record.get("source", "local")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must be a nonempty string")
    elements = normalize_elements(record, image.size)
    original_id = str(record.get("id", path.stem))
    if not original_id:
        raise ValueError("record id must be nonempty")
    group_id = str(record.get("group_id", original_id))
    if not group_id:
        raise ValueError("group_id must be nonempty")
    meta = {
        "source": source,
        "source_id": original_id,
        "group_id": group_id,
        "license": str(record.get("license", "unspecified")),
        "synthetic": False,
        "annotation_scope": "provided_elements_only",
    }
    return image, elements, meta


def font(size):
    return ImageFont.load_default(size=size)


def synthetic_screen(index, seed):
    rng = rng_for(seed, f"synthetic:{index}")
    width, height = rng.choice([(960, 640), (1120, 720), (800, 640)])
    dark = index % 2 == 1
    background, panel, ink = ("#141922", "#242c39", "#f0f3f9") if dark else ("#eff3f9", "#ffffff", "#172339")
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    elements = []

    def add(identity, box, text, category="text", sensitive=False, pii_type=None, fill=None):
        if fill:
            paint_box(image, box, fill)
        draw.text((box[0] + 10, box[1] + 8), text, fill=ink, font=font(18))
        elements.append(
            {
                "id": identity,
                "bbox": list(box),
                "category": category,
                "category_method": "synthetic_ground_truth",
                "sensitivity": "sensitive" if sensitive else "non_sensitive",
                "sensitivity_method": "synthetic_ground_truth",
                "pii_type": pii_type,
                "retained_area_fraction": 1.0,
                "visible_fraction": 1.0,
            }
        )

    section = rng.choice(["Account settings", "Contact details", "Profile overview"])
    add("title", (30, 24, width - 30, 68), f"DEMO / {section}")
    suffix = f"{seed % 10000:04d}{index:05d}"
    for row, (label, value, kind) in enumerate(
        [
            ("Name", f"Example Person {index + 1}", "person_name"),
            ("Email", f"demo{suffix}@example.invalid", "email"),
            ("Phone", f"+1 202 555 {100 + index % 100:04d}", "phone"),
            ("API key", f"DEMO_ONLY_NOT_VALID_{suffix}", "credential"),
        ]
    ):
        y = 105 + row * 105
        add(f"label-{row}", (40, y, 220, y + 36), label)
        add(f"value-{row}", (235, y, width - 40, y + 48), value, "input", True, kind, panel)
    add("save", (40, height - 95, 210, height - 45), "Save profile", "button", fill="#6498ed")
    add("note", (235, height - 95, width - 30, height - 45), "Synthetic training fixture")
    return (
        image,
        elements,
        {
            "source": "synthetic",
            "source_id": f"synthetic-{seed}-{index}",
            "group_id": f"synthetic-{seed}-{index}",
            "license": "generated_fixture",
            "synthetic": True,
            "annotation_scope": "generated_ui_elements",
            "theme": "dark" if dark else "light",
        },
    )


def transform_boxes(elements, sx, sy, offset_x, offset_y, size):
    result, removed = [], []
    for original in elements:
        element = copy.deepcopy(original)
        a, b, c, d = original["bbox"]
        raw = [a * sx - offset_x, b * sy - offset_y, c * sx - offset_x, d * sy - offset_y]
        box = [max(0, raw[0]), max(0, raw[1]), min(size[0], raw[2]), min(size[1], raw[3])]
        if box[0] >= box[2] or box[1] >= box[3]:
            removed.append(element["id"])
            continue
        area = (raw[2] - raw[0]) * (raw[3] - raw[1])
        fraction = (box[2] - box[0]) * (box[3] - box[1]) / area
        element["bbox"] = box
        element["retained_area_fraction"] *= fraction
        result.append(element)
    return result, removed


def mark_occlusion(elements, cover):
    for element in elements:
        region = cover.crop(pixel_box(element["bbox"]))
        hidden = ImageStat.Stat(region).sum[0] / 255
        element["visible_fraction"] *= max(0.0, 1 - hidden / (region.width * region.height))


def augment(base, base_elements, variant, rng):
    image, elements = base.copy(), copy.deepcopy(base_elements)
    width, height = image.size
    details = {"type": variant}
    if variant == "original":
        return image, elements, details
    if variant == "theme":
        image = ImageOps.invert(image)
        details["method"] = "rgb_inversion_approximation_not_native_theme_render"
    elif variant == "scale":
        scale = rng.choice([0.6, 0.8, 1.25])
        size = (round(width * scale), round(height * scale))
        image = image.resize(size, Image.Resampling.LANCZOS)
        elements, _ = transform_boxes(elements, size[0] / width, size[1] / height, 0, 0, size)
        details["scale_xy"] = [size[0] / width, size[1] / height]
    elif variant == "crop":
        crop_width, crop_height = round(width * 0.8), round(height * 0.8)
        left, top = rng.randint(0, width - crop_width), rng.randint(0, height - crop_height)
        image = image.crop((left, top, left + crop_width, top + crop_height))
        elements, removed = transform_boxes(elements, 1, 1, left, top, image.size)
        details.update(
            crop_xyxy=[left, top, left + crop_width, top + crop_height], removed_element_ids=removed
        )
    elif variant in {"occlusion", "cursor", "overlay", "pii"}:
        cover = Image.new("L", image.size, 0)
        draw = ImageDraw.Draw(image)
        if variant == "cursor":
            x, y = rng.randint(0, width - 25), rng.randint(0, height - 35)
            points = [
                (x, y),
                (x + 3, y + 29),
                (x + 10, y + 22),
                (x + 18, y + 32),
                (x + 23, y + 28),
                (x + 15, y + 18),
                (x + 24, y + 18),
            ]
            draw.polygon(points, fill="white", outline="black")
            ImageDraw.Draw(cover).polygon(points, fill=255)
            details["polygon"] = points
        else:
            panel_width = min(width, max(60, min(420, round(width * 0.7))))
            panel_height = min(height, 78 if variant == "pii" else max(40, round(height * 0.22)))
            left, top = rng.randint(0, width - panel_width), rng.randint(0, height - panel_height)
            box = [left, top, left + panel_width, top + panel_height]
            paint_box(image, box, "#efe6c8" if variant == "pii" else "#727783")
            paint_box(cover, box, 255)
            details["box"] = box
            if variant in {"pii", "overlay"}:
                value = (
                    f"fixture{rng.randrange(100000):05d}@example.invalid"
                    if variant == "pii"
                    else "Demo notification"
                )
                text_font = font(16)
                # Narrow screenshots still get a fully contained, legible fixture.
                while (
                    draw.textbbox((0, 0), value, font=text_font)[2] > panel_width - 8 and text_font.size > 5
                ):
                    text_font = font(text_font.size - 1)
                # Clip to the panel so even a tiny input never draws outside its ground-truth box.
                panel = image.crop(tuple(box))
                ImageDraw.Draw(panel).text((4, 6), value, fill="#151922", font=text_font)
                image.paste(panel, (left, top))
        mark_occlusion(elements, cover)
        if variant in {"pii", "overlay"}:
            element_id = "injected-" + variant
            while any(e["id"] == element_id for e in elements):
                element_id += "-new"
            elements.append(
                {
                    "id": element_id,
                    "bbox": box,
                    "category": "text",
                    "category_method": "synthetic_ground_truth",
                    "sensitivity": "sensitive" if variant == "pii" else "non_sensitive",
                    "sensitivity_method": "synthetic_ground_truth",
                    "pii_type": "email" if variant == "pii" else None,
                    "retained_area_fraction": 1.0,
                    "visible_fraction": 1.0,
                }
            )
    else:
        raise ValueError(f"unknown augmentation: {variant}")
    return image, elements, details


def target_mask(size, elements, include_unknown=False):
    mask = Image.new("L", size, 0)
    for element in elements:
        if element["sensitivity"] == "sensitive" or (include_unknown and element["sensitivity"] == "unknown"):
            paint_box(mask, element["bbox"], 255)
    return mask


def mask_metrics(predicted, target):
    if predicted.size != target.size:
        raise ValueError("mask dimensions do not match")
    predicted = predicted.point(lambda p: 255 if p else 0)
    target = target.point(lambda p: 255 if p else 0)

    def count(image):
        return int(ImageStat.Stat(image).sum[0] / 255)

    intersection = count(ImageChops.multiply(predicted, target))
    predicted_count, target_count = count(predicted), count(target)
    union = predicted_count + target_count - intersection
    return {
        "target_pixels": target_count,
        "redacted_pixels": predicted_count,
        "true_positive_pixels": intersection,
        "false_positive_pixels": predicted_count - intersection,
        "false_negative_pixels": target_count - intersection,
        "precision": intersection / predicted_count if predicted_count else None,
        "recall": intersection / target_count if target_count else None,
        "iou": intersection / union if union else None,
    }


def write_sample(root, image, elements, meta, transform, sample_id, split, redact_unknown):
    for element in elements:
        validate_box(element["bbox"], *image.size)
        if not 0 <= element["visible_fraction"] <= 1 or not 0 < element["retained_area_fraction"] <= 1.000001:
            raise ValueError("invalid element visibility after augmentation")
    mask = target_mask(image.size, elements, redact_unknown)
    redacted = image.copy()
    redacted.paste((0, 0, 0), (0, 0), mask)
    relative = {name: f"{name}/{sample_id}.png" for name in ("images", "redacted", "masks")}
    for name, artifact in (("images", image), ("redacted", redacted), ("masks", mask)):
        artifact.save(root / relative[name])
    # Verify the serialized outputs, including pixels outside the requested redaction.
    with Image.open(root / relative["masks"]) as saved:
        saved_mask = saved.convert("L")
    with Image.open(root / relative["redacted"]) as saved:
        saved_redacted = saved.convert("RGB")
    if ImageChops.difference(saved_redacted, redacted).getbbox() is not None:
        raise ValueError("redacted PNG pixel verification failed")
    metrics = mask_metrics(saved_mask, target_mask(image.size, elements, redact_unknown))
    if metrics["false_positive_pixels"] or metrics["false_negative_pixels"]:
        raise ValueError("saved mask does not match annotation boxes")
    unresolved = sum(e["sensitivity"] == "unknown" for e in elements)
    candidates = sum(e["sensitivity_method"] == "text_rule_candidate" for e in elements)
    review = not meta["synthetic"] or unresolved > 0 or candidates > 0
    grayscale = image.convert("L")
    record = {
        "schema_version": 1,
        "id": sample_id,
        "split": split,
        **meta,
        "image": relative["images"],
        "redacted_image": relative["redacted"],
        "mask": relative["masks"],
        "width": image.width,
        "height": image.height,
        "mode": image.mode,
        "image_sha256": digest_image(image),
        "mean_luminance": ImageStat.Stat(grayscale).mean[0],
        "bbox_format": "xyxy",
        "bbox_units": "pixels",
        "elements": elements,
        "augmentation": transform,
        "checks": {
            "bbox_consistency": "passed",
            "saved_redaction_pixels": "passed",
            "redaction_mask_vs_annotation": metrics,
            "metric_scope": "mask_rasterization_only_not_pii_detection_accuracy",
            "unknown_sensitivity_count": unresolved,
            "rule_candidate_count": candidates,
            "needs_review": review,
        },
    }
    dump_json(root / "annotations" / f"{sample_id}.json", record)
    return record


def run(args):
    variants = [] if args.variants == "none" else args.variants.split(",")
    if len(set(variants)) != len(variants) or any(v not in VARIANTS for v in variants):
        raise ValueError("variants must be unique comma-separated names: " + ",".join(VARIANTS))
    if args.synthetic < 0 or (not args.manifest and args.synthetic == 0):
        raise ValueError("provide --manifest and/or a positive --synthetic count")
    manifest = args.manifest.resolve() if args.manifest else None
    output = args.output.resolve()
    if output.exists():
        raise ValueError(
            "output already exists; choose a new directory (existing files are never overwritten)"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    try:
        with tempfile.TemporaryDirectory(prefix=".building-", dir=output) as temporary:
            root = Path(temporary)
            for name in ("images", "redacted", "masks", "annotations", "splits"):
                (root / name).mkdir()
            counts, split_counts, sources = Counter(), Counter(), Counter()
            seen, identities, duplicates, rejected = {}, set(), [], []
            groups, sample_count = {}, 0
            split_handles = {
                name: (root / "splits" / f"{name}.jsonl").open("w", encoding="utf-8")
                for name in ("train", "validation", "test")
            }

            def inputs():
                if manifest:
                    for line, raw in read_records(manifest):
                        try:
                            yield load_record(raw, manifest)
                        except (ValueError, TypeError, OSError, Image.DecompressionBombError) as exc:
                            if not args.skip_invalid:
                                raise ValueError(f"manifest line {line}: {exc}") from exc
                            rejected.append({"line": line, "error_type": type(exc).__name__})
                for index in range(args.synthetic):
                    yield synthetic_screen(index, args.seed)

            try:
                with (root / "manifest.jsonl").open("w", encoding="utf-8") as handle:
                    for image, elements, meta in inputs():
                        identity = json.dumps([meta["source"], meta["source_id"]], separators=(",", ":"))
                        if identity in identities:
                            raise ValueError(
                                "duplicate source/id pair; use unique ids for each source screenshot"
                            )
                        identities.add(identity)
                        image_hash = digest_image(image)
                        if image_hash in seen:
                            duplicates.append(
                                {
                                    "source": meta["source"],
                                    "source_id": meta["source_id"],
                                    "kept": seen[image_hash],
                                }
                            )
                            continue
                        seen[image_hash] = {"source": meta["source"], "source_id": meta["source_id"]}
                        # A globally shared group_id keeps sessions/sites/apps together across sources.
                        group = meta["group_id"]
                        split = split_for(group, args.seed)
                        groups[group] = split
                        parent_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
                        for variant in ["original", *variants]:
                            transformed, labels, operation = augment(
                                image, elements, variant, rng_for(args.seed, identity + ":" + variant)
                            )
                            record = write_sample(
                                root,
                                transformed,
                                labels,
                                {**meta, "parent_id": parent_id},
                                operation,
                                parent_id + "-" + variant,
                                split,
                                args.redact_unknown,
                            )
                            serialized = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
                            handle.write(serialized)
                            split_handles[split].write(serialized)
                            counts[variant] += 1
                            split_counts[split] += 1
                            sources[meta["source"]] += 1
                            sample_count += 1
            finally:
                for handle in split_handles.values():
                    handle.close()
            if not sample_count:
                raise ValueError("no valid screenshots were found")
            report = {
                "schema_version": 1,
                "seed": args.seed,
                "input_screenshots_kept": len(seen),
                "output_samples": sample_count,
                "variants": dict(counts),
                "splits": dict(split_counts),
                "sources": dict(sources),
                "group_count": len(groups),
                "duplicates_skipped": duplicates,
                "rejected_records": rejected,
                "redact_unknown": args.redact_unknown,
                "split_policy": "80/10/10 hash by group_id; small datasets may have empty splits",
                "deduplication": "exact RGB pixel duplicates; first annotation record wins",
                "checks": "all emitted boxes in bounds; saved masks and redacted PNG pixels verified",
                "limitations": [
                    "No OCR, learned element detection, or comprehensive PII detection.",
                    "Text rules produce review candidates; unmatched input elements remain unknown.",
                    "Masks cover annotated boxes, including occluded parts; not character segmentation.",
                    "Redaction precision/recall measures rasterization against labels, not model accuracy.",
                    "Imported screenshots require annotation review, including unannotated areas.",
                    "Theme augmentation is pixel inversion; synthetic originals use light/dark palettes.",
                    "Near duplicates and template/site leakage require meaningful group_id values.",
                ],
            }
            dump_json(root / "report.json", report)
            dump_json(
                root / "run_config.json",
                {
                    "seed": args.seed,
                    "synthetic": args.synthetic,
                    "variants": variants,
                    "redact_unknown": args.redact_unknown,
                    "skip_invalid": args.skip_invalid,
                },
            )
            for path in root.iterdir():
                shutil.move(str(path), str(output / path.name))
    except BaseException:
        # Remove only an empty directory created by this run. Never delete user files.
        try:
            output.rmdir()
        except OSError:
            pass
        raise
    print(f"Created {sample_count} samples from {len(seen)} screenshots in {output}")
    print(f"Report: {output / 'report.json'}")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="JSONL file using the documented common schema")
    parser.add_argument(
        "--synthetic", type=int, default=0, metavar="N", help="generate N annotated synthetic screenshots"
    )
    parser.add_argument("--output", type=Path, required=True, help="new output directory; never overwritten")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--variants", default=",".join(VARIANTS), help="comma-separated augmentation names, or none"
    )
    parser.add_argument(
        "--redact-unknown", action="store_true", help="also mask elements with unknown sensitivity"
    )
    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="skip invalid image/annotation records and report line numbers",
    )
    args = parser.parse_args(argv)
    try:
        run(args)
    except (ValueError, TypeError, OSError, Image.DecompressionBombError) as exc:
        parser.exit(2, f"Curation failed: {exc}\n")


if __name__ == "__main__":
    main()
