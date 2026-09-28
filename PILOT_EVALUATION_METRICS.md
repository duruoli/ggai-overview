# Google AI Overview Pilot Evaluation Metrics

## Purpose and scope

This document defines the initial evaluation framework for the exploratory pilot of Google AI Overview responses to HealthSearchQA consumer medical questions.

The pilot focuses on two domains:

1. **Evidence grounding**
2. **Communication quality**, comprising understandability and actionability

The purpose is to establish a small, interpretable set of metrics suitable for LLM-based evaluation. No overall composite score will be calculated at this stage.

## Unit of evaluation

The primary evaluation unit is one AI Overview response to one question in one run.

Evidence-grounding judgments are made at the level of individual medical claims and then aggregated to the response level. Communication quality is evaluated at the response level.

If no AI Overview is generated for a query:

- record the response as `aio_present = false`;
- exclude it from answer-quality calculations;
- include it when reporting AI Overview availability.

## 1. Evidence grounding

Evidence grounding assesses whether the citations are verifiable, support the medical claims made in the answer, adequately cover those claims, and come from suitable sources.

### 1.1 Citation validity

**Evaluation question:** Does the cited source exist, and do its URL, title, and source identity correspond to a real source?

Each citation is classified as:

- `valid`: the source exists and its identity is correctly represented;
- `invalid_or_fabricated`: the source does not exist or its identity is materially misrepresented;
- `unverifiable`: existence cannot be determined because of access or technical limitations.

A paywall, temporary connection failure, or automated-access restriction does not by itself constitute citation fabrication.

```text
citation_validity_rate = valid citations / verifiable citations
```

The number of invalid or fabricated citations should also be reported separately.

### 1.2 Citation entailment

**Evaluation question:** Does the cited source actually support the medical claim to which it is attached?

Each claim-citation pair with an explicit local HTML mapping is classified as:

- `supported` = 1: the source directly and adequately supports the claim;
- `partially_supported` = 0.5: the source supports only part of the claim, or the answer adds an unsupported qualification or inference;
- `unsupported` = 0: the source is irrelevant, does not contain the claimed information, or contradicts the claim;
- `unverifiable`: the relevant source content cannot be accessed.

```text
citation_entailment_rate =
  sum(entailment values) / number of verifiable claim-citation pairs
```

Entailment must be assessed using the content of the cited source itself. An unsupported local citation should not be treated as supported merely because a different external source confirms the claim. Sources found only in the response-level flat citation list are not assigned to a unique paragraph for this metric.

Two levels of results are retained:

1. **Citation level:** judge every explicit claim-citation pair independently. This is the primary
   measure of whether each citation is reliable.
2. **Claim level:** aggregate all local citations attached to the claim. One fully supporting
   citation is sufficient. If multiple citations are individually partial but may support
   complementary parts of the claim, perform a second joint judgment and mark the result as
   `jointly_supported` only when their combined evidence covers the full claim.

The pair-level results remain visible even when the combined claim-level judgment is supported.

### 1.3 Citation coverage

**Evaluation question:** What proportion of externally verifiable medical claims in the answer are supported by at least one source in the response-level citation pool?

The LLM evaluator first separates the answer into atomic medical claims. Purely conversational statements, headings, generic disclaimers, and invitations for follow-up are excluded from the denominator.

A claim is counted as covered only when at least one source in the complete citation pool provides full or partial support for it. This does not require guessing which paragraph a flat-list-only source was originally intended to support.

```text
citation_coverage_rate =
  sum(best entailment value for each medical claim) /
  total number of externally verifiable medical claims
```

Thus:

- fully supported claim = 1;
- partially supported claim = 0.5;
- unsupported or uncited claim = 0.

If an answer contains medical claims but no citations, coverage is `0`. Citation entailment is `N/A` because there are no claim-citation pairs to evaluate.

### 1.4 Source quality

**Evaluation question:** Is the source sufficiently credible and appropriate for supporting the type of medical claim being made?

Sources are classified as:

- `high`: government or public-health agencies, professional societies, clinical guidelines, systematic reviews, or major academic medical institutions;
- `moderate`: reliable hospital patient-information pages, peer-reviewed primary studies, or clearly identified professional organizations;
- `low`: commercial health websites, private clinic pages, general media, promotional material, individual creators, or sources without clear professional review;
- `unclassifiable`: insufficient information to classify the source.

Source quality should consider both the source type and its fitness for the claim. For example, a reputable patient-information page may be sufficient for a stable definition, while a precise mortality estimate or treatment recommendation may require a guideline, systematic review, or directly relevant study.

```text
high_quality_source_rate =
  high-quality cited sources / classifiable cited sources
```

Moderate- and low-quality source rates should remain available in the evaluation output for interpretation.

## 2. Communication quality

Communication quality is assessed using a simplified, text-focused adaptation of the Patient Education Materials Assessment Tool (PEMAT). It contains two separate constructs: understandability and actionability.

### 2.1 Understandability

Each applicable item is scored `yes = 1` or `no = 0`.

1. **Clear purpose and direct answer:** The response directly addresses the user's question, and its main purpose is evident.
2. **Everyday language:** The response primarily uses language understandable to a non-medical audience.
3. **Explanation of medical terminology:** Necessary medical terms or abbreviations are explained when first used.
4. **Organization:** Information is presented in a clear, logical sequence and divided into manageable sections where helpful.
5. **Focus and concision:** The response avoids unnecessary repetition, distracting material, and excessive detail that obscures the answer.

```text
understandability_score =
  yes items / applicable understandability items * 100
```

### 2.2 Actionability

Each applicable item is scored `yes = 1` or `no = 0`. Items may be marked `N/A` when the question does not reasonably call for an action.

1. **Identifiable action:** When appropriate, the response clearly states at least one action the user can take.
2. **Specific and manageable guidance:** Recommended actions are concrete and feasible rather than limited to vague advice such as "consult a doctor."
3. **Care-seeking guidance:** When appropriate, the response explains when, why, or where the user should seek medical care.

```text
actionability_score =
  yes items / applicable actionability items * 100
```

For a purely definitional question that does not require an action, actionability should be reported as `N/A`, not `0`.

## 3. Minimum response-level output

```json
{
  "aio_present": true,
  "evidence_grounding": {
    "citation_validity_rate": 1.0,
    "invalid_or_fabricated_citation_count": 0,
    "citation_entailment_rate": 0.83,
    "citation_coverage_rate": 0.75,
    "high_quality_source_rate": 0.67
  },
  "communication": {
    "understandability_score": 80,
    "actionability_applicable": true,
    "actionability_score": 67
  },
  "notes": [
    "One numerical claim was only partially supported.",
    "The main recommendation was actionable, but care-seeking timing was vague."
  ]
}
```

The evaluator should retain claim-level and citation-level judgments for auditing even though the primary reported results are response-level metrics.

## 4. Reporting recommendations

Report the following six principal metrics separately rather than combining them into an overall score:

1. Citation validity rate
2. Citation entailment rate
3. Citation coverage rate
4. High-quality source rate
5. Understandability score
6. Actionability score

For each metric, report the number of evaluable responses, missing or `N/A` values, and an appropriate measure of distribution such as mean and standard deviation or median and interquartile range.

Because each question has repeated runs, response-level results should retain the question identifier and run number so that within-question variation can be examined later.

## 5. Practical evaluation notes

- Use the main AI Overview answer rather than concatenated source-card snippets or interface text.
- Treat the flat citation list as the response-level source pool. Recover explicit local claim-citation relationships from the saved HTML only for the stricter local-entailment analysis.
- Record source access time because web pages and AI Overview outputs may change.
- Save a brief evidence excerpt and rationale for each entailment judgment to make LLM decisions auditable.
- Treat obvious medical errors encountered during citation checking as qualitative notes only in this pilot; they are not part of the current formal metrics.
- Do not calculate a single overall quality score in the exploratory phase.

## 6. Methodological basis

- The [SourceCheckup framework](https://doi.org/10.1038/s41467-025-58551-6) motivates separating URL validity, statement-level source support, and response-level support in medical question answering.
- The [AHRQ Patient Education Materials Assessment Tool](https://www.ahrq.gov/health-literacy/patient-education/pemat.html) provides the basis for separating understandability from actionability in patient-facing information.
- The [DISCERN instrument](https://doi.org/10.1136/jech.53.2.105) provides additional background on evaluating the reliability and quality of consumer health information, particularly for treatment-related content.

## 7. Future evaluation dimensions

Some of the following dimensions are outside the scope of the current exploratory pilot and may be developed in later phases:

- Accuracy
- Completeness
- Safety
- Citation validity
- Citation entailment
- Citation coverage
- Source appropriateness
- Patient usefulness
- Understandability/actionability
- Stability
