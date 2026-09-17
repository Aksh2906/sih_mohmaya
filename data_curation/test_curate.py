"""Focused checks for standalone curation; does not load the application."""

import contextlib
import io
import json
import random
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import curate
from PIL import Image


class CurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_curation(self, output="result", **overrides):
        settings = dict(
            manifest=None,
            synthetic=2,
            output=self.root / output,
            seed=42,
            variants=",".join(curate.VARIANTS),
            redact_unknown=False,
            skip_invalid=False,
        )
        settings.update(overrides)
        with contextlib.redirect_stdout(io.StringIO()):
            report = curate.run(Namespace(**settings))
        rows = [json.loads(line) for line in (settings["output"] / "manifest.jsonl").read_text().splitlines()]
        return report, rows

    def manifest(self, records):
        path = self.root / "input.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in records))
        return path

    def element(self, box, **extra):
        return {
            "id": "a",
            "bbox": box,
            "sensitivity": "sensitive",
            "category": "input",
            "visible_fraction": 1.0,
            "retained_area_fraction": 1.0,
            **extra,
        }

    def test_synthetic_end_to_end_and_reproducible(self):
        report, rows = self.run_curation()
        self.assertEqual(report["output_samples"], 16)
        self.assertEqual({r["augmentation"]["type"] for r in rows}, {"original", *curate.VARIANTS})
        for row in rows:
            with Image.open(self.root / "result" / row["image"]) as image:
                self.assertEqual(image.size, (row["width"], row["height"]))
            self.assertEqual(row["checks"]["redaction_mask_vs_annotation"]["recall"], 1)
            self.assertFalse(row["checks"]["needs_review"])
        for parent in {r["parent_id"] for r in rows}:
            self.assertEqual(len({r["split"] for r in rows if r["parent_id"] == parent}), 1)
        _, second = self.run_curation(output="second")
        self.assertEqual(rows, second)

    def test_import_normalized_unknown_and_duplicate(self):
        Image.new("RGB", (100, 80), "white").save(self.root / "a.png")
        Image.new("RGB", (100, 80), "gray").save(self.root / "b.png")
        records = [
            {
                "id": "a",
                "source": "ScreenSpot",
                "group_id": "one-session",
                "image": "a.png",
                "bbox_units": "normalized",
                "elements": [{"bbox": [0.1, 0.25, 0.6, 0.5], "role": "button", "text": "Continue"}],
            },
            {"id": "duplicate", "image": "a.png"},
            {"id": "b", "group_id": "one-session", "image": "b.png"},
        ]
        report, rows = self.run_curation(manifest=self.manifest(records), synthetic=0, variants="none")
        self.assertEqual(report["input_screenshots_kept"], 2)
        self.assertEqual(len(report["duplicates_skipped"]), 1)
        label = rows[0]["elements"][0]
        self.assertEqual(label["bbox"], [10, 20, 60, 40])
        self.assertEqual(label["category"], "button")
        self.assertEqual(label["sensitivity"], "unknown")
        self.assertNotIn("text", label)
        self.assertTrue(rows[0]["checks"]["needs_review"])
        self.assertEqual(rows[0]["split"], rows[1]["split"])
        self.assertIsNone(rows[0]["checks"]["redaction_mask_vs_annotation"]["precision"])

    def test_geometry_clips_scales_and_removes(self):
        elements = [self.element([10, 20, 50, 60]), self.element([0, 0, 5, 5], id="b")]
        transformed, removed = curate.transform_boxes(elements, 2, 2, 40, 60, (100, 100))
        self.assertEqual(removed, ["b"])
        self.assertEqual(transformed[0]["bbox"], [0, 0, 60, 60])
        self.assertEqual(transformed[0]["retained_area_fraction"], 0.5625)
        self.assertEqual(elements[0]["bbox"], [10, 20, 50, 60])

    def test_occlusion_visibility(self):
        cover = Image.new("L", (100, 100), 0)
        curate.paint_box(cover, [10, 20, 30, 60], 255)
        elements = [self.element([10, 20, 50, 60])]
        curate.mark_occlusion(elements, cover)
        self.assertEqual(elements[0]["visible_fraction"], 0.5)

    def test_redaction_metrics_detect_extra_and_missing_pixels(self):
        target, predicted = Image.new("L", (10, 10)), Image.new("L", (10, 10))
        curate.paint_box(target, [0, 0, 4, 4], 255)
        curate.paint_box(predicted, [2, 0, 6, 4], 255)
        metrics = curate.mask_metrics(predicted, target)
        self.assertEqual(metrics["false_positive_pixels"], 8)
        self.assertEqual(metrics["false_negative_pixels"], 8)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)
        self.assertAlmostEqual(metrics["iou"], 1 / 3)

    def test_sensitive_and_unknown_redaction_preserve_other_pixels(self):
        Image.new("RGB", (100, 80), "white").save(self.root / "a.png")
        path = self.manifest(
            [
                {
                    "image": "a.png",
                    "elements": [
                        {"bbox": [10, 10, 20, 20], "text": "demo@example.invalid"},
                        {"bbox": [30, 30, 40, 40]},
                    ],
                }
            ]
        )
        _, rows = self.run_curation(manifest=path, synthetic=0, variants="none", redact_unknown=True)
        with Image.open(self.root / "result" / rows[0]["redacted_image"]) as image:
            self.assertEqual(image.getpixel((10, 10)), (0, 0, 0))
            self.assertEqual(image.getpixel((35, 35)), (0, 0, 0))
            self.assertEqual(image.getpixel((20, 20)), (255, 255, 255))
        self.assertEqual(rows[0]["checks"]["rule_candidate_count"], 1)

    def test_invalid_boxes_and_explicit_labels(self):
        for box in ([0, 0, 0, 1], [-1, 0, 1, 1], [0, 0, 101, 1], [0, 0, float("nan"), 1], [False, 0, 1, 1]):
            with self.subTest(box=box), self.assertRaises(ValueError):
                curate.validate_box(box, 100, 100)
        self.assertEqual(
            curate.sensitivity({"sensitivity": "non_sensitive", "text": "demo@example.invalid"})[0],
            "non_sensitive",
        )

    def test_failed_import_cleanup_and_skip_invalid(self):
        path = self.manifest([{"image": "missing.png"}])
        with self.assertRaises(ValueError):
            self.run_curation(manifest=path)
        self.assertFalse((self.root / "result").exists())
        report, _ = self.run_curation(manifest=path, skip_invalid=True, synthetic=1, variants="none")
        self.assertEqual(report["rejected_records"][0]["line"], 1)

    def test_existing_output_is_untouched(self):
        output = self.root / "result"
        output.mkdir()
        (output / "keep.txt").write_text("unchanged")
        with self.assertRaises(ValueError):
            self.run_curation()
        self.assertEqual((output / "keep.txt").read_text(), "unchanged")
        self.assertEqual(list(output.iterdir()), [output / "keep.txt"])

    def test_small_image_injection_stays_in_bounds(self):
        image = Image.new("RGB", (64, 64), "white")
        for variant in curate.VARIANTS:
            with self.subTest(variant=variant):
                augmented, elements, _ = curate.augment(image, [], variant, random.Random(42))
                for element in elements:
                    curate.validate_box(element["bbox"], *augmented.size)
                if variant == "pii":
                    box = elements[0]["bbox"]
                    for y in range(64):
                        for x in range(64):
                            if not (box[0] <= x < box[2] and box[1] <= y < box[3]):
                                self.assertEqual(augmented.getpixel((x, y)), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
