# Standalone screen dataset curation

An offline Python script implementing the supplied dataset-curation flowchart. It does not import, edit, or run the Veil application. All code, documentation, and tests live in this new folder. Input images are read-only, and an existing output directory is always rejected.

## Run the working demo

From the project root, use the existing Python environment:

```sh
.venv/bin/python standalone_data_curation/curate.py \
  --synthetic 12 \
  --output /tmp/curation-demo \
  --seed 42
```

This produces **96 samples**: 12 synthetic screens, each with an original and seven independently augmented variants. Choose a fresh output path for each run. No internet, API key, browser, dataset download, or application server is needed.

For a separate installation instead:

```sh
python3 -m venv /tmp/curation-env
/tmp/curation-env/bin/python -m pip install -r standalone_data_curation/requirements.txt
/tmp/curation-env/bin/python standalone_data_curation/curate.py --synthetic 12 --output /tmp/curation-demo
```

Python 3.10+ and Pillow 12 are required. No project dependency file needs changing.

## How it follows the flowchart

| Flowchart stage | Implemented behavior |
| --- | --- |
| Existing datasets: RICO, WebUI, Mind2Web, ScreenSpot | Reads local screenshots and annotations through the explicit common JSONL schema below. Preserve the dataset name in `source`. |
| Synthetic screenshot generator | Draws annotated profile/settings screens with light/dark palettes, controls, and fabricated names, emails, phone numbers, and demo credentials. These are raster UI fixtures, not screenshots of a live browser. |
| Theme variation | RGB inversion of imported/generated screenshots as an approximate appearance augmentation. Synthetic originals also use actual light/dark drawing palettes. |
| Resolution scaling and cropping | Resizes and crops images, transforms and clips every box, records retained area, and removes fully cropped-out elements. |
| Masking/occlusion | Adds an opaque obstruction and records the remaining visible fraction of each element. |
| Synthetic PII injection | Adds a fabricated email panel with an explicit sensitive label and box. |
| Cursor and overlay injection | Adds a cursor silhouette or notification panel and measures overlap with existing elements. |
| Element classification | Uses supplied categories or a small role-to-category mapping; otherwise marks the element `unknown`. |
| Sensitivity tagging | Preserves explicit tags; text rules propose email/credential/phone candidates; otherwise marks the element `unknown`. |
| Bounding-box grounding | Validates explicit pixel or normalized `xyxy` boxes and exports pixel-coordinate labels. |
| Redaction precision checks | Writes box masks/redacted images, reloads PNGs, and verifies mask precision/recall/IoU against annotated target boxes and exact redacted pixels. |
| Consistency checks | Rejects invalid/nonfinite/out-of-bounds boxes and repeated IDs, checks transformed geometry, removes exact duplicate input images, and keeps related samples in the same split. |
| Curated output and metadata | Writes images, per-image JSON labels, full/split JSONL manifests, source lineage, image hashes, dimensions, luminance, augmentation parameters, and a run report. |

**Dataset import boundary:** this is a common-schema importer, not a downloader or a native parser for all four datasets. Export each dataset's locally available screenshot paths and annotations to the contract below first. Raw RICO view hierarchies, Mind2Web action/DOM records, and arbitrary WebUI or ScreenSpot releases are not automatically interpreted. No bounding boxes are invented from DOM text or action descriptions. Dataset acquisition, licenses, and native-format conversion remain separate steps.

## Import your real screenshots

Create a UTF-8 `.jsonl` file with one object per screenshot. Paths are relative to the manifest location unless absolute. This is one complete line:

```json
{"id":"screen-001","source":"RICO","group_id":"app-or-session-001","license":"record-your-source-license","image":"screenshots/screen-001.png","bbox_units":"pixels","elements":[{"id":"email-field","bbox":[120,200,480,250],"category":"input","text":"demo@example.invalid","sensitivity":"sensitive","pii_type":"email"},{"id":"save-button","bbox":[120,300,260,350],"role":"button","sensitivity":"non_sensitive"}]}
```

The example coordinates require an image at least 480 × 350. The example path is a placeholder; point it at your actual file.

```sh
.venv/bin/python standalone_data_curation/curate.py \
  --manifest /absolute/path/to/input.jsonl \
  --synthetic 20 \
  --output /absolute/path/to/new-curated-output \
  --seed 42
```

Only `image` is required at the record level. Defaults are `source="local"`, `id=<image filename stem>`, `group_id=<id>`, `bbox_units="pixels"`, and `elements=[]`. Supply explicit, unique IDs for large datasets.

- `bbox` is required for each element: `[left, top, right, bottom]`, with exclusive right/bottom edges. Use `bbox_units="normalized"` for coordinates between 0 and 1. Fractional boxes are rounded outward only when rasterizing masks.
- `category`: `button`, `input`, `text`, `image`, `link`, `checkbox`, `container`, or `unknown`. If absent, optional `role` supplies a classification hint.
- `sensitivity`: `sensitive`, `non_sensitive`, or `unknown`. Explicit labels take priority over text rules. `pii_type` is an optional descriptive label for sensitive elements.
- Optional `text` is used only for sensitivity hints and is **not copied into exported labels**. The script does not OCR screenshot pixels. Source IDs, group IDs, and custom `pii_type` values are exported verbatim, so use identifiers rather than private values.
- `group_id` should identify a site/app/session/template family, not merely a screenshot, when preventing related-layout leakage matters. IDs are global across sources. Every original and its variants remain together. The seed and group ID deterministically select an approximately 80/10/10 train/validation/test split; tiny datasets can have empty splits.
- Exact RGB duplicates retain the first record's labels; skipped IDs are reported. Conflicting duplicate annotations are not merged. Near duplicates are not detected.
- Imported images must be at least 64 × 64. Nontrivial EXIF orientation is rejected because applying it without transforming source boxes would corrupt labels.

## Options

```sh
# Select just three augmentation families (plus the original).
.venv/bin/python standalone_data_curation/curate.py --synthetic 5 \
  --variants scale,crop,pii --output /tmp/curation-selected

# Validate/import originals without augmenting.
.venv/bin/python standalone_data_curation/curate.py --manifest /path/input.jsonl \
  --variants none --output /tmp/curation-originals

# Also redact annotated elements whose sensitivity is unknown.
.venv/bin/python standalone_data_curation/curate.py --manifest /path/input.jsonl \
  --redact-unknown --output /tmp/curation-conservative
```

Available variants: `theme,scale,crop,occlusion,pii,cursor,overlay`. Each variant is derived independently from the original, not cumulatively. Every invocation always includes originals.

By default, any invalid record stops the run. `--skip-invalid` skips unreadable images or invalid element records and lists their manifest line numbers/error types in `report.json`. Malformed JSON, repeated source/ID pairs, and internal output-check failures still stop the run. Outputs are staged in a temporary subdirectory and published after validation; a failed build removes that staging directory. If publication itself is interrupted, choose a new output directory for the next run.

## Output

```text
new-curated-output/
  images/             Original and augmented RGB PNGs
  redacted/           Copies with selected element boxes blacked out
  masks/              Grayscale PNGs: 255 = redact, 0 = retain
  annotations/        Per-image JSON with elements, metadata, and checks
  splits/             train.jsonl, validation.jsonl, test.jsonl
  manifest.jsonl      All emitted samples; paths relative to output root
  report.json         Counts, duplicates, rejected records, limitations
  run_config.json     Seed and processing settings
```

`retained_area_fraction` measures geometry remaining after a crop. `visible_fraction` measures visibility after an injected obstruction within the retained box. Occluded elements keep their semantic labels; redaction conservatively covers their entire remaining box, including covered pixels. The output is a custom training manifest, not automatically a COCO or YOLO export.

The `images/` directory intentionally retains unredacted input content for training. Redaction only covers selected annotated boxes. Unannotated PII, photographs, faces, and unmatched text are not detected by this script. Imported samples are always marked `needs_review`; regex candidates and unknown labels are not human-verified ground truth. `--redact-unknown` covers unknown annotated elements, not unannotated image areas.

A mask score of 1.0 means the saved mask matches the selected boxes. It is **not** an accuracy claim about detecting real sensitive information, and all-empty precision/recall denominators produce `null`. Reproducibility assumes the same input data, order, seed, Python, and Pillow versions.

## Check the script

```sh
.venv/bin/python -m unittest discover -s standalone_data_curation -p 'test_*.py' -v
```

The tests run in temporary directories. They exercise real image I/O, normalized boxes, crop/scale geometry, overlap accounting, mask errors, deterministic generation, duplicate handling, split grouping, rejection/cleanup, and overwrite protection.
