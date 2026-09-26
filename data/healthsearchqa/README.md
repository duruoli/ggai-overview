# HealthSearchQA pilot sample

This directory contains the official HealthSearchQA supplementary workbook and a reproducible simple random sample for the Google AI Overview pilot.

## Source

- Article: https://doi.org/10.1038/s41586-023-06291-2
- Official supplementary data: https://static-content.springer.com/esm/art%3A10.1038%2Fs41586-023-06291-2/MediaObjects/41586_2023_6291_MOESM6_ESM.xlsx
- Source sheet: `All HealthSearchQA Questions`
- Local source file: `healthsearchqa_official.xlsx`
- Source SHA-256: `a89f6639ee76717e2a1ea25bbe25c8c69cf396681be76fd8145da7e9c8917e1e`

## Sampling protocol

1. Read the first column of `All HealthSearchQA Questions`.
2. Trim leading and trailing whitespace and remove empty rows.
3. Retain the first occurrence of each exact question string.
4. Shuffle the unique records with a Mulberry32-seeded Fisher-Yates algorithm.
5. Select the first 40 records.
6. Exclude the off-domain record `HSQA_1548` and replace it in place with the next eligible record in the seeded shuffle (`HSQA_0954`).

Parameters and counts:

- Non-empty source questions: 3,173
- Unique questions after exact deduplication: 3,156
- Exact duplicates removed: 17
- Sample size: 40
- Random seed: 20,260,926
- Excluded off-domain record: `HSQA_1548` (`What are the 3 wind types?`)
- Replacement record: `HSQA_0954` (`Is a popliteal cyst the same as a Baker's cyst?`)
- Selected-ID SHA-256: `914e70399d84730f254a26fcf56b8c107b16cce6c23148b78c9b92d5ff407b53`

No other semantic filtering was applied.

## Files

- `healthsearchqa_official.xlsx`: unmodified official supplementary workbook.
- `healthsearchqa_subset_n40_seed20260926.csv`: flat sample table.
- `healthsearchqa_subset_n40_seed20260926.json`: sample plus provenance metadata.
- `../../outputs/healthsearchqa_pilot/healthsearchqa_subset_n40_seed20260926.xlsx`: formatted review workbook with sample and metadata sheets.
- `../../scripts/generate_healthsearchqa_subset.py`: standalone reproducible sampling script using only the Python standard library.
- `../../scripts/healthsearchqa_aio_capture.user.js`: general Tampermonkey auto-capture and JSON export userscript for new question sets.
- `../../scripts/README_healthsearchqa_aio_capture.md`: installation, capture protocol, output, and verification notes.

## Reproduce

From the repository root:

```bash
python3 scripts/generate_healthsearchqa_subset.py \
  --source data/healthsearchqa/healthsearchqa_official.xlsx \
  --output-dir data/healthsearchqa \
  --sample-size 40 \
  --seed 20260926
```

The script downloads the official workbook automatically if the source file does not already exist.
