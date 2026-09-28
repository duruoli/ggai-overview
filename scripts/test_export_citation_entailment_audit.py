import unittest

from export_citation_entailment_audit import build_rows, markdown_text


class CitationEntailmentAuditTests(unittest.TestCase):
    def setUp(self):
        self.retrieval = {
            "records": [
                {
                    "dataset_id": "HSQA_0001",
                    "run": 1,
                    "question": "Example question?",
                    "pages": [
                        {
                            "citation_id": "src_001",
                            "citation_title": "Example source",
                            "final_url": "https://example.org/final",
                            "access_method": "direct_html",
                        }
                    ],
                    "claim_citation_pairs": [
                        {
                            "pair_id": "HSQA_0001::c001::src_001",
                            "claim_id": "c001",
                            "claim_text": "Example claim.",
                            "citation_id": "src_001",
                            "citation_url": "https://example.org",
                            "page_retrieval_status": "success",
                            "top_chunks": [
                                {
                                    "rank": 1,
                                    "chunk_id": "chunk_0001",
                                    "heading": "Evidence",
                                    "text": "Example evidence passage.",
                                    "retrieval_score": 0.5,
                                }
                            ],
                        }
                    ],
                }
            ]
        }

    def test_marks_pairs_pending_without_entailment_file(self):
        rows = build_rows(self.retrieval)
        self.assertEqual(rows[0]["entailment_label"], "pending")
        self.assertEqual(rows[0]["citation_title"], "Example source")

    def test_joins_judgment_and_chunks_by_pair_id(self):
        entailment = {
            "records": [
                {
                    "claim_citation_pairs": [
                        {
                            "pair_id": "HSQA_0001::c001::src_001",
                            "entailment_label": "supported",
                            "confidence": "high",
                            "reason": "The passage states the claim.",
                            "evidence": [
                                {"chunk_id": "chunk_0001", "quote": "Example evidence"}
                            ],
                            "warnings": [],
                        }
                    ]
                }
            ]
        }
        rows = build_rows(self.retrieval, entailment)
        self.assertEqual(rows[0]["entailment_label"], "supported")
        self.assertEqual(rows[0]["top_chunks"][0]["chunk_id"], "chunk_0001")
        rendered = markdown_text(rows, "retrieval.json", "entailment.json")
        self.assertIn("Example claim.", rendered)
        self.assertIn("Example evidence passage.", rendered)
        self.assertIn("`supported`", rendered)


if __name__ == "__main__":
    unittest.main()
