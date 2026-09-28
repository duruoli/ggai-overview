import unittest

from evaluate_citation_entailment import (
    Confidence,
    EntailmentJudgment,
    EntailmentLabel,
    EvidenceQuote,
    validate_evidence,
)


class CitationEntailmentTests(unittest.TestCase):
    def test_accepts_exact_evidence_quote(self):
        pair = {
            "top_chunks": [
                {
                    "chunk_id": "chunk_0001",
                    "text": "Bacterial infections are the most common cause of sepsis.",
                }
            ]
        }
        judgment = EntailmentJudgment(
            label=EntailmentLabel.supported,
            evidence=[
                EvidenceQuote(
                    chunk_id="chunk_0001",
                    quote="Bacterial infections are the most common cause of sepsis.",
                )
            ],
            reason="The passage directly states the claim.",
            confidence=Confidence.high,
        )
        self.assertEqual(validate_evidence(pair, judgment), [])

    def test_warns_on_nonexistent_quote(self):
        pair = {"top_chunks": [{"chunk_id": "chunk_0001", "text": "Different text."}]}
        judgment = EntailmentJudgment(
            label=EntailmentLabel.supported,
            evidence=[EvidenceQuote(chunk_id="chunk_0001", quote="Invented evidence")],
            reason="Test",
            confidence=Confidence.low,
        )
        self.assertEqual(len(validate_evidence(pair, judgment)), 1)

    def test_accepts_quote_from_rendered_markdown(self):
        pair = {
            "top_chunks": [
                {
                    "chunk_id": "chunk_0001",
                    "text": "People benefit from [modulator therapy](https://example.org/modulators) and _specialized care_.",
                }
            ]
        }
        judgment = EntailmentJudgment(
            label=EntailmentLabel.supported,
            evidence=[
                EvidenceQuote(
                    chunk_id="chunk_0001",
                    quote="People benefit from modulator therapy and specialized care.",
                )
            ],
            reason="The rendered passage states the claim.",
            confidence=Confidence.high,
        )
        self.assertEqual(validate_evidence(pair, judgment), [])


if __name__ == "__main__":
    unittest.main()
