# AIO parsing and medical-claim extraction

The evaluation pipeline separates deterministic HTML parsing from semantic claim extraction:

```text
captured AIO HTML
  -> answer blocks and local citation anchors
  -> atomic medical claims
  -> later citation fetching and metric evaluation
```

This separation prevents source-card snippets and interface text from entering the answer and preserves the local claim-citation structure available in the captured HTML.

The final flat `citations` list is treated as the response-level `source_pool`. Local HTML anchors are retained as additional mapping evidence, but the pipeline does not assume that every source in the flat list belongs to one identifiable paragraph.

## Output layout

Pilot artifacts are grouped by purpose:

```text
outputs/healthsearchqa_pilot/
├── inputs/       # captured input files
├── queues/       # files waiting for manual or follow-up processing
└── evaluation/
    ├── reports/  # human-readable Markdown reports
    ├── results/  # JSON and CSV evaluation artifacts
    └── citation_cache/  # reusable citation and browser cache
```

## 1. Parse the captured HTML

From the repository root:

```bash
python scripts/parse_aio.py \
  "outputs/healthsearchqa_pilot/inputs/healthsearchqa_aio_capture_2026-09-26 (1).json" \
  outputs/healthsearchqa_pilot/evaluation/results/parsed_aio.json
```

The parser:

- selects the saved `main-col` answer container;
- extracts paragraphs, headings, and list items;
- removes citation-widget labels from the answer text;
- maps citation anchors to their exact surrounding text blocks as `local_citation_ids`;
- records citations elsewhere in the same list as conservative `candidate_citation_ids`, because Google often displays a citation marker only after the final bullet;
- retains the full captured source catalog and identifies sources without a local mapping;
- records hidden `+N` markers and citation placeholders as mapping ambiguity.

## 2. Inspect model-ready claim-extraction inputs

This command does not call an API:

```bash
python scripts/extract_medical_claims.py \
  outputs/healthsearchqa_pilot/evaluation/results/parsed_aio.json \
  outputs/healthsearchqa_pilot/evaluation/results/claim_extraction_input.json \
  --prepare-only
```

## 3. Run LLM claim extraction

Set `OPENAI_API_KEY`, choose an available model explicitly, and run:

```bash
python scripts/extract_medical_claims.py \
  outputs/healthsearchqa_pilot/evaluation/results/parsed_aio.json \
  outputs/healthsearchqa_pilot/evaluation/results/extracted_claims.json \
  --model MODEL_NAME
```

For OpenRouter, the repository-local `.openrouter_env` can be loaded directly:

```bash
python scripts/extract_medical_claims.py \
  outputs/healthsearchqa_pilot/evaluation/results/parsed_aio.json \
  outputs/healthsearchqa_pilot/evaluation/results/extracted_claims_sample.json \
  --provider openrouter \
  --env-file .openrouter_env \
  --model openai/gpt-5-mini \
  --limit 3
```

To extract one response per question from a repeated-run capture, select the run explicitly:

```bash
python scripts/extract_medical_claims.py \
  outputs/healthsearchqa_pilot/evaluation/results/parsed_aio.json \
  outputs/healthsearchqa_pilot/evaluation/results/extracted_claims_run1.json \
  --provider openrouter \
  --env-file .openrouter_env \
  --model openai/gpt-5-mini \
  --run 1
```

For an initial quality check, limit execution to a small number of responses:

```bash
python scripts/extract_medical_claims.py \
  outputs/healthsearchqa_pilot/evaluation/results/parsed_aio.json \
  outputs/healthsearchqa_pilot/evaluation/results/extracted_claims_sample.json \
  --model MODEL_NAME \
  --limit 5
```

The script uses Structured Outputs so every claim includes a block identifier, an exact source quote, and one standalone atomic claim. Local and same-list candidate citation identifiers are inherited deterministically from the parsed block rather than assigned by the model. Inferred same-list mappings remain explicitly marked as ambiguous for later entailment checking. The full response-level source pool is copied into the extraction output for later grounding evaluation.

The intended grounding analysis is:

- use explicit local mappings for the strict claim-citation entailment metric;
- use the complete response-level source pool to determine whether each claim is supported by at least one cited source;
- do not infer a unique paragraph mapping for flat-list-only sources.

## 4. Retrieve evidence passages from local citations

For a three-question retrieval pilot:

```bash
python scripts/retrieve_citation_evidence.py \
  outputs/healthsearchqa_pilot/evaluation/results/extracted_claims_gpt5mini_run1.json \
  outputs/healthsearchqa_pilot/evaluation/results/evidence_retrieval_preview_3.json \
  --cache-dir outputs/healthsearchqa_pilot/evaluation/citation_cache \
  --limit-questions 3 \
  --top-k 5
```

Each URL is cached once. The script extracts and chunks the main HTML content, then ranks chunks
for each explicit local claim-citation pair with TF-IDF similarity. If a source rejects direct
automated access, a Jina Reader text fallback is attempted and recorded as
`jina_reader_fallback`. Security challenges and empty/paywalled pages remain distinct retrieval
failures; they must not be interpreted as invalid or non-supporting citations.

This stage retrieves candidate evidence only. It does not make an entailment judgment.

## 5. Judge citation entailment

After inspecting retrieval quality, evaluate each local claim-citation pair using only its
retrieved passages:

```bash
python scripts/evaluate_citation_entailment.py \
  outputs/healthsearchqa_pilot/evaluation/results/evidence_retrieval_preview_3.json \
  outputs/healthsearchqa_pilot/evaluation/results/entailment_preview_3.json \
  --env-file .openrouter_env \
  --model openai/gpt-5-mini
```

The output distinguishes `supported`, `partially_supported`, `not_supported`, `contradicted`,
`insufficient_evidence`, and deterministic `unavailable` cases. Model-provided evidence quotes
are checked against the supplied chunks. Page-access failures are never converted into negative
entailment judgments.

For a targeted quality-control batch, repeat `--dataset-id` for the desired records. The source
retrieval file is not modified:

```bash
python scripts/evaluate_citation_entailment.py RETRIEVAL.json QC_OUTPUT.json \
  --dataset-id HSQA_0440 --dataset-id HSQA_2116 --dataset-id HSQA_2424
```

Evidence quotes are validated against the reader-visible text. Markdown-only link targets and
emphasis markers are removed for matching, while the quoted words themselves must still be a
contiguous substring. Existing results can be checked again without another API call using
`--revalidate-only`.

### Export a human-readable entailment audit

Join every claim and citation with all passages supplied to the LLM, its support label, selected
evidence quotes, reason, confidence, and validation warnings:

```bash
python scripts/export_citation_entailment_audit.py \
  outputs/healthsearchqa_pilot/evaluation/results/evidence_retrieval_preview_3.json \
  outputs/healthsearchqa_pilot/evaluation/reports/entailment_audit_preview_3.md \
  --entailment-json outputs/healthsearchqa_pilot/evaluation/results/entailment_preview_3.json \
  --output-csv outputs/healthsearchqa_pilot/evaluation/results/entailment_audit_preview_3.csv
```

The Markdown is intended for direct human review; the CSV is convenient for filtering and
annotation. If `--entailment-json` is omitted, every pair is exported with the label `pending`,
which is useful for inspecting retrieval before the LLM evaluation is run.

### Human-in-the-loop capture for blocked pages

Prepare the queue without opening a browser:

```bash
python scripts/capture_blocked_citations.py \
  outputs/healthsearchqa_pilot/evaluation/results/evidence_retrieval_preview_3.json \
  --cache-dir outputs/healthsearchqa_pilot/evaluation/citation_cache \
  --prepare-only
```

Remove `--prepare-only` to start a dedicated Chrome session. Complete each verification or login
in Chrome, return to the terminal, and press Enter. The script saves the HTML, extracts and chunks
the main content, recalculates top passages for affected pairs, and updates the retrieval JSON in
place. Enter `s` to skip the current page or `q` to stop safely.

Manual-capture files are stored under
`outputs/healthsearchqa_pilot/evaluation/citation_cache/manual/`. Browser downloads go to its
`downloads/` subdirectory. The dedicated browser profile is retained so session cookies can be
reused for subsequent blocked pages.

After manual recovery, update only the previously unavailable entailment pairs:

```bash
python scripts/evaluate_citation_entailment.py \
  outputs/healthsearchqa_pilot/evaluation/results/evidence_retrieval_preview_3.json \
  outputs/healthsearchqa_pilot/evaluation/results/entailment_preview_3.json \
  --env-file .openrouter_env \
  --model openai/gpt-5-mini \
  --resume-unavailable
```

### Import pages downloaded manually

If a page is visible in a normal browser but cannot be reached by the automated fetcher, first
create a queue with deterministic filenames:

```bash
python scripts/import_manual_citation_content.py \
  outputs/healthsearchqa_pilot/evaluation/results/evidence_retrieval_run1.json \
  --cache-dir outputs/healthsearchqa_pilot/evaluation/citation_cache \
  --prepare-only
```

The queue is written to `citation_cache/manual/imports/import_queue.json`. For each page, save the
browser page under the exact `save_as` filename in that same directory. HTML, MHTML, Markdown, and
plain text are accepted. HTML/MHTML is preferred; CSS, images, and other asset folders are not
needed. The saved file must contain the article/post text visible to the reader, rather than only
a login, cookie, or JavaScript shell. For Reddit, a plain-text copy of the cited post and relevant
comments is an acceptable fallback when saved HTML does not contain the rendered text.

Then run the same command without `--prepare-only`:

```bash
python scripts/import_manual_citation_content.py \
  outputs/healthsearchqa_pilot/evaluation/results/evidence_retrieval_run1.json \
  --cache-dir outputs/healthsearchqa_pilot/evaluation/citation_cache
```

The importer stores a SHA-256 audit record, sanitizes and parses the content, quality-checks and
chunks it, recalculates top-k passages for every affected claim-citation pair, updates the clean
cache, and updates the retrieval JSON in place. Failed quality checks do not replace an existing
successful retrieval. An abstract-only PMC recovery is included in the import queue so it can be
upgraded to manually downloaded full text.

## 6. Score understandability and actionability

Score the main AI Overview text against the response-level, simplified PEMAT-informed rubric
in `PILOT_EVALUATION_METRICS.md`:

```bash
python scripts/evaluate_communication.py \
  outputs/healthsearchqa_pilot/evaluation/results/parsed_aio.json \
  outputs/healthsearchqa_pilot/evaluation/results/communication_evaluation.json \
  --env-file .openrouter_env \
  --model openai/gpt-5-mini
```

The LLM receives only the question and parsed `main_text`. It returns a yes/no/N/A judgment,
reason, and optional exact answer quote for each of five understandability and three
actionability items. The prompt asks it to judge from the perspective of an ordinary reader
with no medical knowledge, without filling in unexplained terms or missing steps from its own
knowledge. The script checks the quotes and N/A rules, then calculates the scores
deterministically. Actionability is N/A when the question does not reasonably call for an action;
the care-seeking item may also be N/A independently. Answers without an AI Overview are retained
as `not_present` and excluded from quality scores. The JSON retains item-level judgments,
question ID, run number, model, token use, and response-level summary statistics.

Use `--limit N` for a short integration run or `--run 1` for one repeated run. If a run is
interrupted, use `--resume` with the same input, output, model, and run filter; successful
judgments are kept and errors are retried. The script refuses to overwrite an existing output
unless `--resume` is specified. `--workers` controls concurrent API requests (default 4).

These scores measure text communication features, not medical accuracy, safety, or measured
patient comprehension. The abbreviated rubric is adapted from PEMAT and is not the original
PEMAT instrument.

## 7. Run tests

```bash
python -m unittest discover -s scripts -p 'test_*.py'
```
