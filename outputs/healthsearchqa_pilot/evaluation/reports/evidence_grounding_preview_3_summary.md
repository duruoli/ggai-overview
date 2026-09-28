# Evidence-grounding pilot: first 3 run-1 questions

## Scope

- 3 questions, 30 extracted claims
- Strict scope: explicit `local_citation_ids` only
- 19 claims had a local citation; each had one local claim-citation pair in this sample
- Citation pages were downloaded once, cleaned, chunked, and ranked with TF-IDF
- Entailment was judged by `openai/gpt-5-mini` using only the top 5 retrieved passages

## Retrieval

- 12 unique cited pages
- 10 pages yielded usable content automatically
- Michigan Medicine and NEJM were recovered through the manual-browser workflow
- 12/12 pages ultimately yielded content, so 19/19 local claim-citation pairs were assessable
- Manual recovery is recorded as `access_method: manual_browser`

## Entailment results

| Label | Count |
|---|---:|
| Supported | 14 |
| Partially supported | 3 |
| Not supported | 2 |
| Contradicted | 0 |
| Unavailable | 0 |

Among the 19 assessable local pairs:

- fully supported: 14/19 (73.7%)
- supported or partially supported: 17/19 (89.5%)

Across all 30 extracted claims:

- strict local citation coverage: 19/30 (63.3%)
- strict fully-supported coverage observed in this retrieval run: 14/30 (46.7%)

## Per-question result

| Question | Claims | Locally cited | Supported | Partial | Not supported | Unavailable |
|---|---:|---:|---:|---:|---:|---:|
| What does a vulvodynia flare up feel like? | 12 | 6 | 4 | 1 | 1 | 0 |
| Is cystic fibrosis usually fatal? | 9 | 9 | 8 | 1 | 0 | 0 |
| What is the most common cause of septic shock? | 9 | 4 | 2 | 1 | 1 | 0 |

## Interpretation constraint

These results evaluate only explicit local citation mappings. Claims without a local citation were
not searched against the response-level source pool, and inaccessible pages were not assigned a
negative entailment label. The sample is intended to validate the pipeline, not estimate final
performance across all 40 questions.
