# HealthSearchQA Google AI Overview capture userscript

`healthsearchqa_aio_capture.user.js` is a general-purpose Tampermonkey userscript
for collecting Google AI Overviews from a new question set. It is no longer tied
to the original fixed 40-question pilot or its redo queue: every non-empty Google
search query is eligible for automatic capture.

## Install

1. Install Tampermonkey in Chrome and enable userscripts if Chrome requests it.
2. Open the Tampermonkey dashboard and choose **Create a new script**.
3. Replace the editor contents with `healthsearchqa_aio_capture.user.js`, then save.
4. Open a normal Google Search results page. The **HealthSearchQA AIO Capture ·
   Auto ON** panel should appear at the bottom right.

The script only activates on Google `/search` pages. New records use a separate
Tampermonkey storage namespace, so captures from the old fixed-40 pilot are not
mixed into this collection.

## Capture protocol

1. Search the first question in the new set using its intended wording.
2. Do not click **Retry Now** during normal collection. After three seconds, the
   script automatically waits for the results and for an AI Overview.
3. If an overview appears, the script expands **Show more**, waits for the text
   to stabilize, then stores the text, citations, and sanitized HTML. If no
   overview appears after the grace period, absence is stored as a valid result.
4. Continue to the next question. Google in-page navigation is detected, so a
   full page reload is not required.
5. After the set is complete, click **Export All** to download one JSON file.

Each normalized query is captured once by default. Repeating the same query does
not create a duplicate. If the protocol needs repeated runs, change
`TARGET_RUNS_PER_QUERY` near the top of the script before beginning collection.

The script retries a failed automatic capture up to two times. **Retry Now** is
kept only as a fallback. If **Show more** cannot be fully expanded, the record is
not saved, which prevents the earlier truncation problem.

Use the same Chrome profile, Google account state, language, location, and search
settings throughout a collection so results remain comparable.

## Controls

- **Retry Now**: fallback retry for the current query; normal capture is automatic.
- **Export All**: downloads all records and the derived per-query progress list.
- **Undo Last**: deletes the most recent record after confirmation.
- **Reset**: deletes all records in the current general-collection namespace.

The panel displays the number of distinct questions and total captures. Because
the script accepts any query, it does not know the new set's total size in
advance. Each query receives a deterministic `QUERY_XXXXXXXX` identifier and an
order number based on when it was first captured. The original query text is
also retained in full.

## Output highlights

Each record includes its generated query ID, normalized query key, collection
order, URL query, run, offset-aware timestamp, search URL, AIO presence/text,
de-duplicated external links, sanitized AIO HTML, extraction diagnostics, and
browser metadata. The export includes compact record summaries and a progress
entry for every query captured in the current collection.

The sanitized HTML keeps text, links, basic DOM structure, and useful selectors,
but removes scripts, styles, SVG, hidden inline content, images/Base64 payloads,
comments, and Google runtime attributes.

## Verification

From the repository root:

```bash
node --check scripts/healthsearchqa_aio_capture.user.js
node scripts/test_healthsearchqa_aio_capture.mjs
```

Google does not publish a stable AIO DOM contract. The extractor therefore uses
multiple known container hints plus a semantic `AI Overview` heading fallback.
Before collecting a large set, run one present-AIO query and one absent-AIO query,
export them, and inspect the JSON.
