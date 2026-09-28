import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from capture_blocked_citations import (
    blocked_queue,
    is_standard_web_page,
    manual_page_record,
    update_retrieval_payload,
)


class BlockedCitationCaptureTests(unittest.TestCase):
    def test_queue_deduplicates_blocked_urls(self):
        payload = {
            "records": [
                {
                    "dataset_id": "q1",
                    "question": "Question",
                    "pages": [
                        {"url": "https://example.org/a", "retrieval_status": "blocked_content"},
                        {"url": "https://example.org/b", "retrieval_status": "success"},
                    ],
                    "claim_citation_pairs": [
                        {"pair_id": "p1", "citation_url": "https://example.org/a"}
                    ],
                },
                {
                    "dataset_id": "q2",
                    "question": "Question 2",
                    "pages": [
                        {"url": "https://example.org/a", "retrieval_status": "no_content"}
                    ],
                    "claim_citation_pairs": [],
                },
            ]
        }
        self.assertEqual([item["url"] for item in blocked_queue(payload)], ["https://example.org/a"])

    def test_manual_capture_extracts_chunks(self):
        html = """<html><head><title>Condition</title></head><body><main>
        <h1>Evidence</h1><p>Bacterial infections are the most common cause of sepsis and septic shock. Infections may begin in the lungs, abdomen, urinary tract, skin, or soft tissues and can trigger a widespread immune response with organ dysfunction.</p>
        </main></body></html>"""
        with TemporaryDirectory() as directory:
            page = manual_page_record(
                "https://example.org/a",
                "https://example.org/a",
                "Condition",
                html,
                Path(directory),
            )
        self.assertEqual(page["retrieval_status"], "success")
        self.assertEqual(page["access_method"], "manual_browser")
        self.assertEqual(len(page["chunks"]), 1)

    def test_updates_pairs_and_summary(self):
        payload = {
            "retrieval_method": {"top_k": 5},
            "summary": {},
            "records": [
                {
                    "dataset_id": "q1",
                    "question": "What causes septic shock?",
                    "pages": [
                        {"url": "https://example.org/a", "retrieval_status": "blocked_content"}
                    ],
                    "claim_citation_pairs": [
                        {
                            "pair_id": "p1",
                            "claim_text": "Bacterial infections commonly cause septic shock.",
                            "citation_url": "https://example.org/a",
                            "page_retrieval_status": "blocked_content",
                            "top_chunks": [],
                        }
                    ],
                }
            ],
        }
        page = {
            "cache_key": "abc",
            "final_url": "https://example.org/a",
            "retrieval_status": "success",
            "http_status": None,
            "content_type": "text/html",
            "page_title": "Example",
            "access_method": "manual_browser",
            "direct_error": None,
            "error": None,
            "chunks": [
                {
                    "chunk_id": "chunk_0001",
                    "heading": "Causes",
                    "text": "Bacterial infections commonly cause septic shock.",
                    "word_count": 6,
                }
            ],
        }
        update_retrieval_payload(payload, "https://example.org/a", page)
        pair = payload["records"][0]["claim_citation_pairs"][0]
        self.assertEqual(pair["page_retrieval_status"], "success")
        self.assertEqual(len(pair["top_chunks"]), 1)
        self.assertEqual(payload["summary"]["page_status_counts"], {"success": 1})

    def test_standard_web_filter_excludes_video_and_reddit(self):
        self.assertTrue(is_standard_web_page("https://www.nejm.org/article"))
        self.assertFalse(is_standard_web_page("https://www.youtube.com/watch?v=abc"))
        self.assertFalse(is_standard_web_page("https://www.reddit.com/r/example/comments/abc"))


if __name__ == "__main__":
    unittest.main()
