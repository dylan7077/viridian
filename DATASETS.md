# Image sources and dataset audit

Data is kept under `data/datasets/` (ignored by Git). A dataset's hosting and
metadata license do not by themselves establish rights in every underlying card
image. Review provenance and intended use before training, releasing weights,
or redistributing data. Do not treat unlabeled catalog scans as mint condition.

## Segmentation photos

- Source: [antokun/pokemon-card-segmentation](https://huggingface.co/datasets/antokun/pokemon-card-segmentation), revision `81df8c64e121f7ec26df02519a19af7218616ded`.
- Dataset card declares CC0-1.0. The archive contains 107 JPGs, each paired
  with a text annotation. All are under the original `train` directory; there
  is no supplied validation split. Three files are exact duplicates.
- Useful for investigating card localization/segmentation, not card condition
  grades. Inspect polygon quality and group near-duplicate shots before splitting.
- Exploratory baseline: `scripts/evaluate_detection_dataset.py` evaluated all 107
  images with no execution failures. Mean polygon IoU was 0.698; 90/107 met 0.5
  and 49/107 met 0.75. It compares the detector's single quadrilateral with the
  largest labeled card. This small, related collection is not a generalization
  benchmark; overlapping shots and annotation quality were not independently audited.

## Authenticity images

- Source: [stevelohwc/pokemon_card_image_for_authenticity_classification](https://huggingface.co/datasets/stevelohwc/pokemon_card_image_for_authenticity_classification), revision `91851c4aa6644071f7b0cf7f43ea1305f8bd1af1`.
- Dataset card declares MIT; 290 JPEG rows, 145 fronts and 145 backs. Labels
  are 196 counterfeit and 94 authentic, not condition scores.
- Two byte-identical front/back image pairs have conflicting authenticity
  labels: the `sample_card_but_detected_as_no_card_20260118_*` files and the
  corresponding `sample_new_counterfeit_card_20260118_*` files. Exclude or
  independently relabel conflicts; never put duplicates in separate splits.
  Group fronts and backs by the underlying physical card, not filenames alone.
- Not suitable as PSA grade or condition-subgrade labels.

## Grading research archive — quarantine

- Source: [jyesr/pokemon-tcg-grading](https://huggingface.co/datasets/jyesr/pokemon-tcg-grading), revision `02e6b335b59b67e2d2df678ce8fcff4b1a0b1d8a`.
- No license is declared. The source card mentions PSA grades but also calls
  them expected grades. Metadata contains certification IDs and grade-like
  strings; that does not independently establish image ownership, that each
  label is an actual official result, or permission to train or publish.
- `metadata.csv` has 52,092 rows, 26,046 distinct IDs and one front and back
  filename per ID. A plurality are grade 10; grades, qualifiers, authenticity
  records and missing labels must not be conflated. These are overall labels,
  not centering/corner/edge/surface subgrades.
- In the first verified shard, 6,473 image entries include 11 filenames absent
  from the CSV. Ten of those are byte-identical PNGs under different names; one
  is a JPG. Visual inspection shows the duplicated PNG is a blurred PSA-logo
  placeholder, not a card photograph. Do not silently match by ID or assume
  all archive files are labeled.
- Archives are downloaded to `data/datasets/quarantine/` without extracting or
  using them for training. `python scripts/download_grading_research.py` resumes
  bounded range downloads and verifies each completed archive's SHA-256 against
  the pinned Hub revision. A `.partial` file is not a verified archive.
- If rights and provenance are resolved, independently audit image/label joins,
  repeated physical cards, capture conditions, leakage from slab labels and
  exact/near duplicates. Hold out entire physical cards and their front/back
  photos together; reserve a separate external evaluation set.

## Other downloaded material

- [Alexleplusmeilleur/pokemon-card-dataset](https://huggingface.co/datasets/Alexleplusmeilleur/pokemon-card-dataset): 40 card photos in quarantine; no license was stated at download time. Do not infer condition labels.
- [GemSnipe centering measurements](https://github.com/rrh1441/pokemon-card-centering-measurements): CC BY 4.0; downloaded CSV has 320 measurements and 302 distinct listing IDs. No source photos are included. `scripts/evaluate_centering_dataset.py` needs the exact photo matched by annotation ID and reports missing rows rather than inventing results. The labels themselves are another CV system's measurements, not independent human ground truth.
- [TCGdex](https://tcgdex.dev/) supplies catalog metadata and reference art for
  the app's identification index. The indexer can resume after interruption;
  rerun it to retry missing images, then rebuild the ORB database. Catalog scans
  do not establish the photographed card's physical condition.

## Continuing collection

On this Windows machine, `scripts/continue_collection.py` supervises the catalog
and quarantined grading archive independently. Status and subsequent logs are
in `data/collection_status/`. The catalog backs off on HTTP 429/503 and retries
after a pause; an `incomplete` or `failed` status is not a completed download.
After a catalog pass the supervisor rebuilds the ORB database. To resume manually:

```powershell
$env:DISCORD_WEBHOOK_URL=''
.venv\Scripts\python.exe scripts/build_index.py --workers 3
.venv\Scripts\python.exe scripts/build_orb_db.py
.venv\Scripts\python.exe scripts/download_grading_research.py
```
