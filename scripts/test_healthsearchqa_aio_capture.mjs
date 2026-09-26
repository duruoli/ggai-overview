import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scriptPath = new URL('./healthsearchqa_aio_capture.user.js', import.meta.url);
const source = fs.readFileSync(scriptPath, 'utf8');

const memory = new Map();
const sandbox = {
  __HSQA_AIO_CAPTURE_SKIP_INIT__: true,
  location: { href: 'https://www.google.com/search?q=test' },
  window: { setTimeout },
  navigator: { userAgent: 'test', language: 'en-US' },
  URL,
  Blob,
  console,
  setTimeout,
  clearTimeout,
  crypto: globalThis.crypto,
  GM_getValue: (key, fallback) => memory.has(key) ? memory.get(key) : fallback,
  GM_setValue: (key, value) => memory.set(key, value),
  GM_deleteValue: (key) => memory.delete(key),
};
sandbox.globalThis = sandbox;
vm.runInNewContext(source, sandbox, { filename: scriptPath.pathname });

const api = sandbox.__HSQA_AIO_CAPTURE_TEST__;
assert.ok(api, 'test API should be exposed');
assert.equal(api.targetRunsPerQuery, 1);

assert.equal(
  api.queryKey('  What are 5 symptoms of Tourette’s?  '),
  "what are 5 symptoms of tourette's?",
  'smart apostrophes and surrounding whitespace should normalize',
);
assert.equal(
  api.queryKey("Is a popliteal cyst the same as a Baker\\'s cyst?"),
  "is a popliteal cyst the same as a baker's cyst?",
  'a source string containing a literal backslash should normalize',
);
assert.equal(api.makeQueryId('A new question'), api.makeQueryId('  A NEW question  '));
assert.match(api.makeQueryId('A new question'), /^QUERY_[0-9A-F]{8}$/);

sandbox.location.href = 'https://www.google.com/search?q=x';
assert.equal(
  api.unwrapGoogleUrl('https://www.google.com/url?q=https%3A%2F%2Fexample.org%2Fpage'),
  'https://example.org/page',
);
assert.equal(
  api.unwrapGoogleUrl('https://publisher.example/article?q=https%3A%2F%2Fdo-not-unwrap.example'),
  'https://publisher.example/article?q=https%3A%2F%2Fdo-not-unwrap.example',
);

assert.match(
  api.localIsoTimestamp(new Date(2026, 8, 26, 15, 30, 0)),
  /^2026-09-26T15:30:00\.000[+-]\d{2}:\d{2}$/,
);

const firstQuestion = 'What does a new symptom feel like?';
const secondQuestion = 'Is this another new question?';
const firstId = api.makeQueryId(firstQuestion);
const secondId = api.makeQueryId(secondQuestion);
const records = [
  {
    dataset_id: secondId,
    query_id: secondId,
    query_key: api.queryKey(secondQuestion),
    sample_order: 2,
    question: secondQuestion,
    run: 1,
    ai_overview_present: false,
    extraction_status: 'not_present',
  },
  {
    dataset_id: firstId,
    query_id: firstId,
    query_key: api.queryKey(firstQuestion),
    sample_order: 1,
    question: firstQuestion,
    run: 1,
    ai_overview_present: true,
    ai_overview_text: 'Complete answer',
    extraction_status: 'success',
  },
];

assert.equal(api.getRunPlan([], firstQuestion).run, 1);
assert.equal(api.getRunPlan(records, firstQuestion), null, 'a captured query should not be duplicated');
assert.equal(api.getRunPlan(records, 'An unseen query').run, 1);
assert.equal(api.nextSampleOrder(records, firstQuestion), 1);
assert.equal(api.nextSampleOrder(records, 'An unseen query'), 3);

const exported = api.makeExportPayload(records);
assert.deepEqual(Array.from(exported.records, (record) => record.sample_order), [1, 2]);
assert.equal(exported.experiment.question_set_mode, 'open_query_collection');
assert.equal(exported.experiment.question_count, 2);
assert.equal(exported.experiment.target_runs_per_query, 1);
assert.equal(exported.experiment.actual_capture_count, 2);
assert.deepEqual(Array.from(exported.progress[0].captured_runs), [1]);
assert.equal(exported.progress[0].query_id, firstId);
assert.equal(exported.progress[0].complete, true);
assert.equal(exported.record_summaries.length, 2);
assert.deepEqual(
  Object.keys(exported),
  ['schema_version', 'export_timestamp', 'experiment', 'record_summaries', 'records', 'progress'],
);

console.log('HealthSearchQA general AIO capture tests passed.');
