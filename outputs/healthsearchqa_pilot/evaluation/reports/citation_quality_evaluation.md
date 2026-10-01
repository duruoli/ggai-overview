# Citation and Communication Quality Evaluation of Google AI Overviews

## Scope

This report summarizes citation quality and communication quality in the HealthSearchQA Google AI Overview pilot. Citation findings combine pair-level LLM judgments with targeted manual review. Communication findings are response-level LLM judgments from the first run.

For citation entailment, the primary unit is an explicitly mapped claim–citation pair. A source found elsewhere in the response-level citation pool does not repair a poorly attached citation for the pair-level metric. Claim-level support is reported separately when multiple citations are explicitly attached to the same claim.

## Evaluation set

| Item | Count |
|---|---:|
| First-run question records | 40 |
| With AI Overview | 39 |
| Extracted medical claims | 388 |
| Claims with at least one explicit local citation | 301 |
| Claims without an explicit local citation | 87 |
| Claims with candidate-only mappings | 77 |
| Claims with no mapping | 10 |
| Explicit claim–citation pairs | 331 |
| Unique locally evaluated pages | 173 |
| Unique URLs in the complete response-level source pool | 352 |

Twenty-six claims have multiple explicit local citations: 22 have two and four have three. The 173 locally evaluated pages were retrieved successfully. The complete 352-URL response-level pool has not yet been evaluated for citation coverage, so pair-level entailment should not be interpreted as full answer-level coverage.

## Overall entailment distribution

### Raw LLM judgments

These are the labels currently stored in `entailment_run1.json`.

| Label | Count | Percentage of 331 pairs |
|---|---:|---:|
| Supported | 181 | 54.7% |
| Partially supported | 127 | 38.4% |
| Not supported | 13 | 3.9% |
| Contradicted | 4 | 1.2% |
| Insufficient evidence | 6 | 1.8% |

### Manually audited interpretation

Manual review of every `insufficient_evidence`, `not_supported`, and `contradicted` result produced the following changes:

- all six `insufficient_evidence` pairs are better classified as `not_supported` because the accessible full pages do not support the claims;
- three of the 13 original `not_supported` pairs are better classified as `partially_supported` because relevant material was omitted by extraction or top-k retrieval;
- two of the four original `contradicted` pairs are better classified as `partially_supported`;
- one original `contradicted` pair is an invalid claim–citation mapping rather than a contradiction;
- one direct contradiction remains.

| Audited label | Count | Percentage of 331 captured pairs |
|---|---:|---:|
| Supported | 181 | 54.7% |
| Partially supported | 132 | 39.9% |
| Not supported | 16 | 4.8% |
| Contradicted | 1 | 0.3% |
| Invalid mapping | 1 | 0.3% |

After excluding the invalid mapping, 181/330 valid pairs are fully supported, 132/330 are partially supported, 16/330 are not supported, and 1/330 is contradicted.

## Reliability of supported judgments

A deterministic spot check of 12 `supported` pairs found no clear false-positive label. Ten were directly supported by explicit source language. Two relied on reasonable semantic inference rather than near-verbatim correspondence, such as interpreting a short duration as a rapid mood shift or follicle blockage as irritation. This is reassuring but is not a substitute for a full manual audit of all 181 supported pairs.

## Detailed analysis of partially supported pairs

There are 127 partial pair judgments involving 120 unique claims.

### Pair-level versus claim-level support

The distinction between source-specific support and claim-level support is material:

- 10 partial claims have another explicitly attached local citation labeled `supported`. These claims are supported at the claim level even though the weaker citation remains partial at the pair level.
- Six partial claims have multiple explicit local citations but no fully supporting citation. Manual inspection did not find a clear case in which their combined evidence repaired every missing element.
- The remaining 104 partial claims have only one explicit local citation and therefore cannot receive joint support under the current mapping.

Examples in which another explicit citation repairs the claim include Baker's cyst causes, gestational-diabetes follow-up testing, viral infections not requiring antibiotics, and the effect of obstructive coronary artery disease on blood flow and oxygen delivery. The partial citation should remain visible because it is still a weak citation for that claim.

Candidate sources inferred from neighboring list items are not treated as attached citations. They may be used later for response-level coverage, but counting them as local support would reward citation misplacement.

### Did claim rewriting create the partial judgments?

The evidence does not show a systematic rewriting artifact:

- all 388 `source_quote` fields are validated exact normalized substrings of the corresponding AI Overview blocks;
- only 70 claim texts are surface-identical to their source quotes, but most changes resolve pronouns, headings, or omitted subjects;
- only one clear claim combines two complete source sentences;
- in the manually reviewed missing-component and qualifier cases, the disputed material was already present in the original AI Overview text or its full block.

The two broad examples discussed during review were also present verbatim as broad AI Overview sentences: the seven-type headache list and the combined COPD treatment statement. They were not created by the claim extractor.

### Extra clauses or list elements not supported by the attached citation

The initial review established that the disputed content was present in the original AI Overview rather than added by claim rewriting. A subsequent review of the complete response-level source pool changed the interpretation: most of this content is supported elsewhere in the same answer, but by a source that is not explicitly attached to the sentence. These are therefore primarily citation-attachment failures, not evidence that the AI Overview invented content for which its source pool contains no support.

| Case | Full original AI Overview item | What the attached local citation says | What other sources in the same response say | Final interpretation |
|---|---|---|---|---|
| Nasopharyngeal-cancer risk (`HSQA_3069::c008`) | “Tobacco and alcohol: People who smoke tobacco or use heavy amounts of alcohol have an increased risk.” | The attached Cancer Research UK page supports smoking but the retrieved page does not mention alcohol. | American Cancer Society, Mayo Clinic, and Cleveland Clinic explicitly state that heavy alcohol use raises nasopharyngeal-cancer risk. | Local pair: reliably `partially_supported`. Response-pool claim: supported. This is a citation-matching problem. |
| Ringworm risk (`HSQA_0552::c011`) | “Having a weak immune system or skin breaks.” | The attached Cleveland Clinic page supports a weak immune system but not skin breaks. | Ubie explicitly lists skin breaks/pre-existing skin damage; MedlinePlus says ringworm is more likely with minor skin injuries. | Local pair: reliably `partially_supported`. Response-pool claim: supported. |
| Baker's cyst eponym (`HSQA_0954::c003`) | “A Baker's cyst and a popliteal cyst are the exact same thing: a fluid-filled swelling that forms a lump at the back of the knee. 'Popliteal cyst' is the medical and anatomical term (named after the popliteal fossa, the hollow space at the back of the knee), while 'Baker's cyst' is the common name, named after the 19th-century surgeon Dr. William Morrant Baker who first described them.” | The attached Cleveland Clinic page supports equivalence of the names but not the eponym history. | The AAOS OrthoInfo source explicitly states that the condition was named after the 19th-century surgeon Dr. William Morrant Baker, who first described it. | Local pair: reliably `partially_supported`. Response-pool claim: supported. |
| Melanoma border description (`HSQA_2089::c005`) | “Border: The edges are irregular, ragged, notched, or blurred.” | The attached Melanoma Research Alliance page describes irregular borders that are difficult to define, supporting irregular/blurred but not explicitly ragged/notched. | The Skin Cancer Foundation source explicitly describes uneven borders with scalloped or notched edges; MD Anderson describes fuzzy or irregular borders. | Local pair: partial under a strict source-specific standard. Response-pool support is substantially stronger; `ragged` is not verbatim but is close to uneven/scalloped. |
| Medication or withdrawal causing chills (`HSQA_2423::c007`) | “Medications and Withdrawal: Drug side effects or withdrawal from substances like alcohol or opioids can trigger cold flashes.” | The attached CARE Hospitals page supports medication side effects causing chills, but does not discuss withdrawal. | Cleveland Clinic lists drug withdrawal as a cause of chills. Sleep Foundation explicitly states that prescription-opioid withdrawal can cause cold flashes with goosebumps; it also links alcohol withdrawal to sweating, not directly to cold flashes. | Local pair: reliably `partially_supported`. Response-pool claim: strongly supported for drug/opioid withdrawal, but alcohol-specific cold flashes remain only indirectly supported. |
| Emotional suppression behaviors (`HSQA_2398::c006`) | “Emotional Suppression: Bottling up feelings, changing the subject, or minimizing a problem instead of processing it.” | Choosing Therapy supports suppressing feelings instead of processing them, but not changing the subject or minimizing a problem. | Lifebulb describes denial as downplaying the impact of difficult truths or emotions, supporting the minimization component. No reviewed source explicitly described changing the subject. | Local pair: reliably `partially_supported`. Response-pool claim: still partial because the changing-the-subject example remains unverified. |
| BPD feature list (`HSQA_0406::c001`) | “You can tell if someone may have Borderline Personality Disorder (BPD) by looking for a consistent pattern of intense emotional instability, unstable relationships, and a shifting self-image.” | The attached video transcript supports repeated emotional patterns and unstable self-image but does not describe unstable relationships. | Mental Health America, the Mental Health Association of Maryland, Mayo Clinic, NHS, and other sources in the same response explicitly describe unstable or intense relationships. | Local pair: reliably `partially_supported`. Response-pool claim: supported. |

These examples should not be described as hallucinations by the claim-extraction model, and most should not be described as globally uncited AI Overview content. The robust finding is narrower: the citation directly attached to the sentence does not support the whole sentence, while another source elsewhere in the response often does.

### Unsupported strength, frequency, or ranking qualifiers

Twenty-two partial claims contain conspicuous strength or frequency terms such as `frequently`, `primarily`, `most common`, `rapidly`, `significantly`, `greatly`, `often`, or `rare`. Every disputed qualifier was already present in the exact AI Overview source quote; none was introduced by claim rewriting.

Representative patterns include:

| AI Overview wording | Citation wording | Interpretation |
|---|---|---|
| Dandruff is **primarily** caused by *Malassezia*. | *Malassezia* is one of several possible causes. | Unsupported causal primacy. |
| Hot flashes are **frequently** followed by chills. | Chills **may** occur after a hot flash. | Possibility was strengthened into frequency. |
| UTI is **the most common** cause of hematuria. | UTI is **a common** cause. | A common cause was promoted to the single most common cause. |
| Essential tremor is **the most common** movement disorder. | It is **one of the most common** movement disorders. | “One of” was dropped. |
| Migraine involves **extreme** sensitivity to light and sound. | The source describes sensitivity but not the stronger intensity. | Unsupported intensity qualifier. |
| A stroke **causes permanent** brain damage. | A stroke **may lead to lasting** brain damage. | Conditional risk was strengthened into a categorical outcome. |

Most of these partial labels are reliable. A small number are borderline because the cited source may entail the qualifier without using identical wording, for example `rapidly shifting` versus `changes frequently`.

### Numerical and temporal details: original-text and source-pool audit

I screened the partial pairs for ages, percentages, measurements, hour/day/month intervals, and quantitative ranges, then manually reviewed the cases in which the numerical or temporal detail could explain the partial label. The quotations below come from validated `source_quote` spans in the captured AI Overview, not from the rewritten `claim_text`; in every case, the numerical detail is unchanged by claim extraction. I then checked the complete locally retrieved page, other retrieved pages in the same response, and, where the distinction mattered, the linked study or clinical guidance. A partial *pair* means the attached citation is inadequate; it does not by itself establish that the number is false or invented. The response-level source pool still has not been exhaustively retrieved, so “not established” below is limited to the checked material.

#### Cases where the attached citation actually says a different number or boundary

1. **COPD lifespan, `HSQA_2432::c004::src_004`**
   - **AIO original:** “Stage 2 and 3 (Moderate to Severe): Lifespan can be reduced by about **2 to 6 years on average**.”
   - **Extracted claim:** “People with stage 2 or 3 (moderate to severe) COPD can have lifespan reduced by about **2 to 6 years on average**.” The extractor added the subject but did not change the range.
   - **Attached citation:** [Top Doctors](https://www.topdoctors.co.uk/medical-articles/part-2-chronic-obstructive-pulmonary-disease-copd-prognosis-and-treatment/) gives approximately **5 to 7 years** for stage 2 and 3.
   - **Mismatch:** **2–6 ≠ 5–7 years** for the same stated stages. This is a clear *local citation* mismatch. It is not proof that 2–6 was invented: a [primary NHANES study](https://uknowledge.uky.edu/internalmedicine_facpub/56/) reports about **2.2** years for stage 2 and **5.8** for combined stages 3–4, but only for **65-year-old male current smokers** and with smoking-related years counted separately. The AIO omits those conditions and presents the range as a general stage-2/3 average.

2. **Obstructive CAD cutoff, `HSQA_1519::c003::src_005`**
   - **AIO original:** “It happens when a waxy buildup called plaque narrows or blocks the large coronary arteries by **50% or more**.”
   - **Extracted claim:** “Obstructive coronary artery disease happens when a waxy buildup called plaque narrows or blocks the large coronary arteries by **50% or more**.” The extractor supplied the antecedent; the cutoff is unchanged.
   - **Attached citations:** [Healthline](https://www.healthline.com/health/coronary-artery-disease/coronary-artery-disease-types) uses **more than 50%** (`>50%`); [MedlinePlus](https://medlineplus.gov/angina.html) supplies no cutoff.
   - **Mismatch:** AIO says **≥50%**, while the numerical citation says **>50%**. They differ **only at exactly 50%**. This is a strict wording/boundary mismatch, not a demonstrated medical error: [published research](https://pmc.ncbi.nlm.nih.gov/articles/PMC6015317/) also uses `≥50%` for obstructive CAD.

**Not a direct numerical mismatch:** `HSQA_0508::c003::src_002` says “Risk: Delays past **12 to 48 hours** can cause permanent scarring and untreatable erectile dysfunction.” Its [attached UCLA video transcript](https://www.youtube.com/watch?v=K4KR6Tu9iWE&t=20) gives **no hour window**, so there is no different citation number to compare; this is missing local evidence. A captured [Cleveland Clinic page](https://my.clevelandclinic.org/health/diseases/10042-priapism) gives **more than 36 hours** for likely scarring in *low-flow* priapism, while an [NHS clinical procedure](https://www.nnuh.nhs.uk/publication/download/joint-trust-clinical-procedure-for-the-management-of-priapism-v2/) describes tissue injury at 12 hours and fibrosis by 48 hours for that subtype. The AIO omits the low-flow condition; its interval must not be read as a safe waiting period.

#### Apparent mismatches resolved by another source, or by reading the attached source precisely

| Pair; exact original AI Overview wording | What the attached citation supplies | Judgment after wider check |
|---|---|---|
| `HSQA_2424::c003::src_002` — “with **over 80% living past age 58**.” (The containing item concerns mild cerebral palsy.) | [Cerebral Palsy Hub](https://www.cerebralpalsyhub.com/cerebral-palsy/life-expectancy/) says many with mild CP live into their 60s and 70s, but gives neither 80% nor age 58. | **Local citation gap, not an invented statistic.** Another captured [Cerebral Palsy Guide page](https://www.cerebralpalsyguide.com/cerebral-palsy/prognosis/life-expectancy/) states the >80%/58-year figure explicitly, citing a [BMC Neurology cohort study](https://pmc.ncbi.nlm.nih.gov/articles/PMC6549269/). The study's conclusion concerns the **22% of its CP population with the mildest impairments**; its population and severity definition should accompany the statistic. |
| `HSQA_2089::c009::src_003` — “The spot is larger than **6 millimeters (about the size of a pencil eraser)**,” | The [NHS page](https://www.nhs.uk/conditions/melanoma-skin-cancer/symptoms-of-melanoma-skin-cancer/) says melanomas are **often** more than 6 mm wide; its “end of a pencil” comparison refers to ordinary moles, not an eraser. | **The 6 mm value agrees.** The [Melanoma Research Foundation](https://melanoma.org/what-does-melanoma-look-like/) and [Skin Cancer Foundation](https://www.skincancer.org/skin-cancer-information/melanoma/melanoma-warning-signs-and-images/) pages in the same answer explicitly compare 6 mm to a pencil eraser. The local NHS pair remains partial for the analogy; the AIO also drops the NHS's “often” qualifier. |
| `HSQA_2598::c011::src_005` and `::src_001` — “Up to half of subsequent **major strokes** happen within **48 hours** of a TIA.” | [Cleveland Clinic](https://my.clevelandclinic.org/health/diseases/14173-transient-ischemic-attack-tia-or-mini-stroke) says **half of strokes following a TIA** occur within **two days**; [Mayo Clinic](https://www.mayoclinic.org/diseases-conditions/transient-ischemic-attack/expert-answers/mini-stroke/faq-20058390) says risk is especially high within 48 hours, without the fraction. | **No percentage/time mismatch** with Cleveland: two days is 48 hours. In this AIO, “major” follows “TIA or mini-stroke” and likely means a subsequent actual stroke in ordinary language, rather than a defined severity subgroup. On that reading the Cleveland pair is substantively supported and its partial label is likely over-strict. If “major” is interpreted technically as only severe strokes, the citation does not give a severity-specific fraction. Mayo alone remains partial because it gives no “half” proportion. |
| `HSQA_1045::c008::src_005` — “According to the Cystic Fibrosis Foundation, the **median age of death has risen significantly over time**,” | The attached [Foundation page](https://www.cff.org/managing-cf/understanding-changes-life-expectancy) reports steady gains in survival, a predicted **median survival age** of 66 for the 2021–2025 birth cohort, and a **median age at death** of 38.8 among 2024 deaths. It does **not** provide a time series of median age at death; these two metrics cannot be compared to prove a trend. | **Citation-placement problem, not a false temporal trend.** The same AIO's captured source list includes an [unmapped study](https://pmc.ncbi.nlm.nih.gov/articles/PMC10497589/) reporting US median age at death rising from **24 in 1999 to 37 in 2020**. It supports the trend at response-pool level, but is not attached to this AIO sentence. The specific attribution to the Foundation page remains inadequately evidenced. |
| `HSQA_1519::c005::src_006` and `::src_007` — “Nonobstructive Coronary Artery Disease: This occurs when the major arteries are **not blocked by 50% or more**, but blood flow is still reduced.” | The attached [Cleveland Clinic](https://my.clevelandclinic.org/health/diseases/16898-coronary-artery-disease) and [Aurora Health Care](https://www.aurorahealthcare.org/services/heart-vascular/conditions/coronary-artery-disease/types) pages do not give the 50% cutoff. | **Local definition gap.** The [Healthline page](https://www.healthline.com/health/coronary-artery-disease/coronary-artery-disease-types) attached to the preceding AIO item gives **less than 50%** for nonobstructive CAD; [NHLBI](https://www.nhlbi.nih.gov/health/coronary-heart-disease) independently gives the same cutoff for a large coronary artery. “Not blocked by 50% or more” expresses `<50%`, so there is no numerical contradiction. |
| `HSQA_0440::c001::src_001` — “Go to the emergency room immediately if your erection is painful **or lasts longer than four hours**” | [Cleveland Clinic](https://my.clevelandclinic.org/health/diseases/10042-priapism) describes priapism as usually painful and often lasting over four hours; it urges ER care for an erection without arousal that persists **a few hours**. | **Four hours is not a numerical mismatch.** The partial concern is the AIO's broader logical **“or”**: the page does not say that every painful erection, irrespective of duration or context, warrants ER care. [Mayo Clinic](https://www.mayoclinic.org/diseases-conditions/priapism/symptoms-causes/syc-20352005) also says an erection over four hours requires emergency care. |
| `HSQA_0508::c001::src_001` — “An erection lasting **longer than four hours** is a medical emergency that requires immediate care at an emergency room to prevent permanent tissue damage.” | [UCSF](https://www.ucsfhealth.org/care/conditions/priapism) recommends evaluation for an erection lasting **four hours**; it identifies **ischemic** priapism as the emergency subtype. | **No time-value conflict.** The partial issue is the unqualified claim that every >4-hour erection is an ischemic emergency and UCSF's lack of an explicit “emergency room” instruction in the evaluated text. [Mayo Clinic](https://www.mayoclinic.org/diseases-conditions/priapism/symptoms-causes/syc-20352005) does advise emergency care after four hours. |

Two other temporal-looking partials do not establish a conflicting number. `HSQA_0484::c013::src_003` says “Rapid swelling **within hours**, deep bruising that tracks down the leg…”; [Utah Health](https://healthcare.utah.edu/orthopaedics/specialties/hip-pain/hamstring-tear-surgery) says swelling appears **a few hours** after injury and bruising **within a few days**. The AIO does not assign the “within hours” interval to bruising; its unsupported elements are bruising that tracks down the leg and a visible dent/gap/knot. `HSQA_0674::c010::src_006` says chronic bacterial prostatitis symptoms “last for **months** but do not include a high fever”; the attached [video](https://www.youtube.com/watch?v=k7WZs-ad2S4&t=12) calls the infection persistent or recurrent but gives no month count. Another captured [NIDDK page](https://www.niddk.nih.gov/health-information/urologic-diseases/prostate-problems/prostatitis-inflammation-prostate) says chronic bacterial prostatitis can last **three or more months**, so the duration is a local citation gap, not an invented interval. The categorical “do not include a high fever” remains stronger than the checked source wording, which describes chronic symptoms as generally less severe than acute symptoms. Neither case is a demonstrated numerical contradiction.

The strongest finding in this subset is **misattached or incomplete numerical evidence**, with one clear local range conflict (COPD). The COPD and priapism examples also show how a plausible number can lose the population or subtype that makes it interpretable. This targeted review does not establish a rate of numerically false AIO statements among all 132 audited partial pairs.

For COPD and priapism, the observable result is loss of conditions needed to interpret the figures. COPD's approximate 2–6-year range resembles a subgroup-specific published estimate, while its attached citation gives 5–7 years. The priapism 12–48-hour interval is absent from the attached video, and clinical evidence for that progression is specific to low-flow priapism. These observations are **consistent with** cross-source synthesis or qualifier omission; the captured data cannot establish which sources the AIO generator actually combined. In particular, the clinical procedure supplying the 12/48-hour milestones was found during this audit, not in the captured citation pool.

### Causal and mechanistic upgrades: original text and response-pool audit

Every quotation below appears in the original AI Overview, so claim rewriting did not create these upgrades. “Local” is the attached citation; “other” is a citation captured with the same answer.

| Pair and exact original AI Overview text | Local citation | Other captured citation | Judgment |
|---|---|---|---|
| `HSQA_1045::c005::src_003` — “Complications: Other multi-system issues, such as end-stage lung disease, severe malnutrition from digestive problems, or organ failure, can also contribute to fatal outcomes.” | [PCNOW](https://www.mypcnow.org/fast-fact/palliative-care-for-patients-with-cystic-fibrosis/): end-stage lung disease causes death; low BMI is *associated* with earlier death. | [MedlinePlus](https://medlineplus.gov/ency/article/000107.htm): lists malnutrition and organ failure as complications; lung disease causes most deaths. | Malnutrition: association becomes causal contribution; not established by the checked sources. Lung disease is supported. |
| `HSQA_3069::c004::src_002` — “People who have had EBV (the virus that causes mono) are at risk because the virus can mix with cells in the nose and throat.” | [Mayo](https://www.mayoclinic.org/diseases-conditions/nasopharyngeal-carcinoma/symptoms-causes/syc-20375529): EBV is linked to cancer; no cell mechanism. | [ACS](https://www.cancer.org/cancer/types/nasopharyngeal-cancer/causes-risks-prevention.html): viral DNA can mix with cell DNA. | Local citation misses mechanism; another captured citation supplies it. |
| `HSQA_2005::c001::src_001` — “Nasal polyps are caused by long-term inflammation in the lining of the nose and sinuses” | [Mayo](https://www.mayoclinic.org/diseases-conditions/nasal-polyps/symptoms-causes/syc-20351888): chronic inflammation is linked to polyps; exact cause unknown. | [Rush](https://www.rush.edu/conditions/nasal-polyps) says inflammation causes polyps; [Penn](https://www.pennmedicine.org/conditions/nasal-polyps) retains uncertainty. | Local correlation → cause; captured sources disagree on certainty. |
| `HSQA_2432::c010::src_005` — “Overall Health: Other conditions like heart disease or diabetes lower overall survival rates.” | [Ubie](https://ubiehealth.com/doctors-note/life-expectancy-copd-factors-treatment): comorbidities affect life expectancy; no direction in evaluated text. | [Solace](https://www.solace.health/articles/copd-life-expectancy): heart disease and diabetes can reduce survival. | Local source omits direction; captured source supports it. |
| `HSQA_0440::c007::src_001` — “Do light physical activity, such as taking a brisk walk or doing squats, to help redirect blood flow” | [Cleveland](https://my.clevelandclinic.org/health/diseases/10042-priapism): suggests activity, without its blood-flow mechanism. | [PMC review](https://pmc.ncbi.nlm.nih.gov/articles/PMC4458797/): mentions exercise (“steal syndrome”), but says efficacy lacks evidence. | The mechanism is a captured hypothesis, not citation-free; efficacy remains uncertain. |
| `HSQA_2447::c009::src_003` — “Anemia: Low iron or red blood cell counts decrease oxygen delivery to the brain.” | [Mayo](https://www.mayoclinic.org/diseases-conditions/dizziness/symptoms-causes/syc-20371787): lists anemia and dizziness, without this causal bridge. | [UPMC](https://share.upmc.com/2025/05/causes-of-dizziness/): low iron reduces oxygen delivery to the brain. | Local mechanism gap; captured source supports it. |
| `HSQA_1648::c009::src_004` — “Pain triggered by shifting estrogen levels during menstruation, pregnancy, or menopause.” | [Healthdirect](https://www.healthdirect.gov.au/headaches): names hormonal changes, not estrogen. | [CommonSpirit](https://www.commonspirit.org/blog/7-common-types-of-headaches-how-to-fight-them): connects estrogen shifts and headache in the named contexts. | Local detail gap; captured source supports it. |
| `HSQA_0508::c005::src_002` — “ischemic (low-flow, trapped blood) or non-ischemic (high-flow, unregulated blood entry).” | [UCLA video](https://www.youtube.com/watch?v=K4KR6Tu9iWE&t=20): names high-flow priapism, but does not explain inflow in the evaluated transcript. | [Cleveland](https://my.clevelandclinic.org/health/diseases/10042-priapism) and [UCSF](https://www.ucsfhealth.org/care/conditions/priapism): injury can cause continuing or uncontrolled arterial inflow. | Local mechanism gap; captured sources support it. |
| `HSQA_2420::c009::src_007` — “Friction and sweat: Tight clothing, heavy perspiration, and friction (especially on the back of the neck, shoulders, thighs, or buttocks) irritate the skin and stress hair follicles.” | [UMass](https://www.ummhealth.org/health-library/understanding-carbuncles): friction and sweat co-occur with carbuncles; no pathway. | [Vujevich](https://www.vucare.com/2025/11/15/what-causes-boils-and-carbuncles/): friction and heat irritate skin and stress follicles. | Local association → mechanism; captured source supports it. |
| `HSQA_1390::c006::src_006` — “Psychological Health: Conditions like anxiety, depression, or perfectionism deeply tint how a person views and accepts themselves.” | [Alliance](https://www.allianceforeatingdisorders.com/body-image-and-society/): lists perfectionism and other personal factors; not anxiety/depression. | [AEIC](https://www.aeiccasemanagement.com/post/understanding-the-roots-5-causes-of-body-image-issues): explicitly says anxiety and depression can contribute to body-image issues. | Local examples missing; captured source supports the direction. |
| `HSQA_0552::c007::src_002` — “Self-spread: You scratch an infected spot and then touch another part of your own body.” | [Mayo](https://www.mayoclinic.org/diseases-conditions/ringworm-body/symptoms-causes/syc-20353780): contact transmission; no scratch → touch sequence. | [CDC causes](https://www.cdc.gov/ringworm/causes/index.html) and [MedlinePlus](https://medlineplus.gov/ency/article/001439.htm): contact spread, without that exact sequence. | Specific self-transfer sequence not established by checked text pages; plausible inference. |
| `HSQA_2801::c002::src_003` — “Urinary tract infections (UTIs): Bacteria enter the urethra and multiply in the bladder or kidneys, causing inflammation and bleeding.” | [Cxbladder](https://www.cxbladder.com/us/patients/bladder-cancer-faqs/blood-in-your-urine/): UTI may cause hematuria; no pathway. | [Riverside](https://www.riversideonline.com/patients-and-visitors/healthy-you-blog/blog/u/understanding-hematuria-causes-diagnosis-and-treatment): bacteria → inflammation → bleeding. | Broad mechanism supported elsewhere; exact urethra → bladder/kidney sequence unchecked. |
| `HSQA_2801::c004::src_004` — “Kidney or bladder stones: Hard mineral deposits scrape and irritate the lining of the urinary tract as they pass or cause blockages.” | [Mayo](https://www.mayoclinic.org/diseases-conditions/blood-in-urine/symptoms-causes/syc-20353432): stones, blockage and hematuria; no scraping mechanism. | [Riverside](https://www.riversideonline.com/patients-and-visitors/healthy-you-blog/blog/u/understanding-hematuria-causes-diagnosis-and-treatment): stones irritate urinary lining and can cause bleeding. | Lining irritation supported elsewhere; “scrape” is more specific. |
| `HSQA_2801::c007::src_004` — “Vigorous exercise: Intense workouts, particularly long-distance running, can cause minor bladder trauma or dehydration.” | [Mayo](https://www.mayoclinic.org/diseases-conditions/blood-in-urine/symptoms-causes/syc-20353432): bladder damage is one possible explanation; no dehydration here. | [Urology San Antonio](https://www.urologysanantonio.com/blood-in-urine-hematuria/): injury or dehydration can explain exercise-related hematuria. | Local gap; captured source supports both as possibilities. |
| `HSQA_2948::c007::src_011` — “Dietary Factors: Foods high in sugar, refined carbohydrates, dairy, and brewer's yeast can cause insulin and hormone spikes that lead to inflammation.” | [Healthline](https://www.healthline.com/health/hidradenitis-suppurativa/HS-make-worse): sugar/dairy may raise insulin and androgens; brewer’s yeast is a separate possible trigger. | [Make HStory](https://www.makehstory.com/hidradenitis-suppurativa-triggers): sugar/refined carbs may spike insulin; yeast is separately listed as a trigger. | The insulin/hormone mechanism is extended to brewer’s yeast without support in the captured pool. |
| `HSQA_2424::c015` (three local pairs) — “on Reddit, some users with personal experience note that the physical strain and comorbidities associated with severe CP can cause premature aging and shortened lifespans regardless of baseline care,” | The [Reddit thread](https://www.reddit.com/r/CerebralPalsy/comments/1jloici/cp_life_expectancymy_brother_died_sensetive_post/) says shortened life and premature aging can occur **regardless of wheelchair use**, not regardless of care. The full [CP Guide](https://www.cerebralpalsyguide.com/cerebral-palsy/prognosis/life-expectancy/) also mentions premature aging. | [CP Hub](https://www.cerebralpalsyhub.com/cerebral-palsy/life-expectancy/): quality of care and medical support affect survival. | The AIO changes the Reddit qualifier from wheelchair use to baseline care. The checked text does not support that change; CP Hub does not logically rule out shortened survival despite good care. |

Three apparent pool-wide gaps were resolved on closer review:

- `HSQA_2598::c005::src_003`: The [full attached Cognitive FX page](https://www.cognitivefxusa.com/blog/tia-vs.-stroke-whats-the-difference-between-a-mini-stroke-and-a-stroke) supports the stroke → brain-tissue death claim; the retrieved excerpts missed it.
- `HSQA_1390::c002::src_002`: The [full attached BALANCE page](https://balancedtx.com/blog/what-influences-body-image/) covers idealized and edited media images; the retrieved excerpts missed them.
- `HSQA_2948::c001::src_008`: The attached [myHSteam page](https://www.myhsteam.com/resources/understanding-common-hidradenitis-suppurativa-hs-triggers) suggests blockage → inflammation, but captured [HS Foundation](https://www.hs-foundation.org/hs-causes) also describes immune-driven inflammation → blockage. This is a source disagreement, not a missing mechanism across the pool.

#### Causal mechanism with no support in the checked citation pool

This does **not** mean the other 15 rows are fully supported. Some have support in a different captured citation; others remain partly unresolved. The row below isolates one specific mechanism absent from the checked pool.

| Pair and exact original AI Overview text | What the captured citations say | 判断 |
|---|---|---|
| `HSQA_2948::c007::src_011` — “Dietary Factors: Foods high in sugar, refined carbohydrates, dairy, and brewer's yeast can cause insulin and hormone spikes that lead to inflammation.” | [Attached Healthline](https://www.healthline.com/health/hidradenitis-suppurativa/HS-make-worse): sugar/dairy → insulin/androgen changes; separately, brewer’s yeast → possible severe reactions. [Captured Make HStory](https://www.makehstory.com/hidradenitis-suppurativa-triggers): sugar/refined carbs → insulin spikes → possible inflammation; separately, brewer’s yeast → possible HS flares. | **把两条不同的路径拼成一条，并套用于整个食物清单。** AIO 说啤酒酵母也会经由“胰岛素／激素升高 → 炎症”起作用；已核查引文只把它列为可能诱因，没有给出这条路径。这是具体机制缺证，不等于“啤酒酵母不会诱发 HS”。 |

The CP claim substitutes “regardless of baseline care” for the Reddit comment's “regardless of wheelchair use.” The former is not established by the checked text; CP Hub says care affects survival, which does not rule out shortened survival despite care. Two cited videos were unavailable, so the claim is excluded from the strict table. This case review does not estimate a rate among the 132 partial pairs.

#### Source distinctions lost in synthesis

These cases show different ways a single AIO sentence can erase source boundaries: the HS diet sentence joins separate pathways; the CP sentence changes a Reddit comment's wheelchair qualifier to care; the nasal-polyp sentence uses Rush's causal wording without Mayo's uncertainty. A first-person account and a clinical source also serve different evidentiary roles, even when both appear as citations.

The output does not show that AIO gives all sources equal weight. [Google says](https://blog.google/products-and-platforms/products/search/ai-overviews-update-may-2024/) AI Overviews use its search ranking systems; it does not disclose how sources are weighed during sentence generation. The observable problem is loss of source attribution, scope, or uncertainty—not a proven absence of source weighting.

### Compound claims and complementary citations

Many partial labels arise because one AI Overview sentence contains several treatments, symptoms, examples, or outcomes. A source may support only a subset.

For example, the COPD statement combines inhalers, oxygen therapy, vaccines, pulmonary rehabilitation, preservation of lung function, and improvement of daily life. Its explicitly attached Crossroads citation supports oxygen therapy, pulmonary rehabilitation, and improved quality of life, but not inhalers, vaccines, or preservation of lung function. Other response-level sources discuss some of the missing interventions, but they are not explicitly attached to this sentence. The pair-level partial judgment is therefore appropriate, while answer-level support may be higher.

The seven-headache example shows a related retrieval problem. The generated reason originally said the CommonSpirit page omitted migraine, cluster, and rebound headache. Full-page inspection shows that it covers six of the seven AI Overview categories, including migraine and cluster, but lists hypertension headache rather than rebound headache. The `partially_supported` label remains correct, but the reason overstated the amount of missing evidence.

### Evidence-quote quality

Across all partial pairs, the evaluator generated 277 evidence quotations:

- 276 are exact normalized substrings of the supplied evidence chunk;
- one differs only because Markdown italics were stripped from organism names.

The principal evidence problem is therefore not fabricated quotation. It is incomplete retrieval: top-k chunks can omit relevant sections of a long page and cause the reason to misdescribe what the full source contains.

## Mapping failure identified during contradiction review

The apparent ice-pack contradiction was not a genuine AI Overview medical error. The AI Overview said that some sources recommend ice packs but that the NHS advises against them. The NHS link was attached to the word “NHS” in the warning clause. Block-level citation inheritance incorrectly paired that NHS citation with the earlier statement that some sources recommend ice packs, creating a false contradiction.

This demonstrates that citation offsets and clause scope must be preserved. Copying every citation in a block to every extracted claim can create invalid pairs.

## Main citation-quality patterns

The current evidence supports the following recurring patterns:

1. **Compound-claim undercoverage:** a citation supports some but not all coordinated items or outcomes.
2. **Unsupported intensification:** the AI Overview changes `may`, `common`, or `one of the most common` into stronger frequency, ranking, or certainty language.
3. **Locally unsupported extra detail or mechanism:** the AI Overview includes examples, descriptors, or a causal explanation absent from the attached source, although another source elsewhere in the response may support it.
4. **Citation-placement mismatch:** a source may be relevant elsewhere in the response but is not attached to the claim it would support.
5. **Source distinctions lost in synthesis:** a sentence may merge separate pathways or drop which source expressed a qualifier or uncertainty.
6. **Block-level mapping error:** citation inheritance across an entire paragraph or list item can create false pairs and false contradictions.
7. **Retrieval-induced reasoning error:** the label may remain correct while the generated reason is incomplete because relevant full-page content was not among the top-k chunks.
8. **Numerical scope loss:** a range or time window may be plausible in a particular study population or disease subtype but presented without those conditions, or attached to a page giving a different estimate.

## Recommended evaluation revision

Use a position-first, semantics-second pipeline:

1. Preserve the original AI Overview sentence and exact text span rather than using the rewritten claim as the sole evaluation text.
2. Preserve citation-anchor offsets in the HTML.
3. Attach an inline link to its containing clause and a trailing citation marker to the immediately preceding sentence or list item.
4. Treat list-level or inherited citations as ambiguous candidates rather than explicit local mappings.
5. Retain pair-level citation entailment as the primary citation-reliability measure.
6. Add a separate claim-level judgment over all explicitly attached citations, including a joint judgment when complementary partial sources may cover the full claim.
7. Evaluate the complete response-level source pool separately for citation coverage.
8. Give the entailment evaluator either the complete cleaned page or a retrieval procedure that can expand beyond top-k passages before concluding that content is absent.
9. Record source-specific qualifications and disagreements before treating multiple sources as joint support for one sentence.

## Work still pending

The following analyses are not yet complete and should not be treated as final findings in this report:

- exhaustive adjudication of all quantitative statements, including those outside the partially supported category and citations in the unreviewed response-level pool;
- semantic rematching of candidate-only citations to exact AI Overview sentence spans;
- response-level coverage over all 352 unique source-pool URLs;
- full manual audit of all 181 supported pairs.

These pending tasks affect answer-level coverage and the fine-grained composition of the partial category, but they do not change the confirmed pair-level mapping and qualifier findings above.

## Communication quality (first run)

Of 40 questions, 39 produced an AI Overview and were scored with a short, PEMAT-informed rubric from the perspective of a reader without medical training. Understandability averaged 90.77/100 across all 39 answers. Actionability applied to seven questions and averaged 85.72/100; the other 32 answers were N/A, not failures. [Item-level results](communication_evaluation_layperson_run1.json) retain the reasons and answer excerpts.

| Item | Yes | No | N/A |
|---|---:|---:|---:|
| Direct answer | 38 | 1 | 0 |
| Everyday language | 38 | 1 | 0 |
| Medical terms explained | 22 | **16** | 1 |
| Logical organization | 39 | 0 | 0 |
| Focus and concision | 39 | 0 | 0 |
| Identifiable action | 7 | 0 | 32 |
| Specific, manageable guidance | 6 | 1 | 32 |
| When, why, or where to seek care | 5 | **2** | 32 |

**Unexplained medical language is the clearest weakness.** Sixteen of 38 applicable answers use terms needed for comprehension without explaining them in plain language. Examples include `FEV1`, `BPPV`, and `GMFCS`, as well as disease names repeated from the question. Overall wording can still sound familiar: only one answer failed the separate everyday-language item.

**Care-seeking guidance is the main actionability gap.** The hamstring-injury answer describes severe signs but does not say when to seek care. The sinusitis answer gives self-care steps but leaves the care-seeking threshold to a follow-up exchange or external guides. The prostatitis answer identifies an urgent presentation but lacks a concrete next step, failing the specific-guidance item.

The single direct-answer failure asked for five Tourette symptoms but received only two. Organization and concision received no negative labels in this small, binary-scored set. The 7/7 identifiable-action result is generous: for the hamstring answer, the LLM counted an invitation to provide more details as an action. These are text-based LLM ratings, not measured patient comprehension or behavior.
