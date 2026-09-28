import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from import_manual_citation_content import (
    extract_reddit_html_blocks,
    import_one,
    import_queue,
)


class ManualCitationImportTests(unittest.TestCase):
    def test_queue_includes_failures_and_abstract_only_successes(self):
        payload = {
            "records": [
                {
                    "dataset_id": "q1",
                    "question": "Question?",
                    "pages": [
                        {
                            "url": "https://example.org/failure",
                            "retrieval_status": "recovery_error",
                            "access_method": "reddit_specialized",
                        },
                        {
                            "url": "https://example.org/abstract",
                            "retrieval_status": "success",
                            "access_method": "pmc_efetch_abstract",
                        },
                        {
                            "url": "https://example.org/full",
                            "retrieval_status": "success",
                            "access_method": "direct_html",
                        },
                    ],
                    "claim_citation_pairs": [
                        {"pair_id": "p1", "citation_url": "https://example.org/failure"}
                    ],
                }
            ]
        }
        queue = import_queue(payload)
        self.assertEqual({item["reason"] for item in queue}, {"retrieval_failed", "abstract_only_upgrade"})
        self.assertEqual(len(queue), 2)

    def test_imports_saved_html_and_extracts_chunks(self):
        html = """<html><head><title>Example</title>
        <link rel="canonical" href="https://example.org/article"></head><body><main>
        <h1>Evidence</h1><p>This complete article paragraph contains enough substantive medical information to be retained as evidence. It explains the condition, its symptoms, possible causes, expected progression, and the relevant clinical context clearly for patients, caregivers, clinicians, and other readers.</p>
        </main></body></html>"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capture.html"
            source.write_text(html, encoding="utf-8")
            page, audit = import_one("https://example.org/article", source, root / "cache")
        self.assertEqual(page["retrieval_status"], "success")
        self.assertEqual(page["access_method"], "manual_html_import")
        self.assertEqual(audit["declared_page_url"], "https://example.org/article")
        self.assertEqual(audit["chunk_count"], 1)
        self.assertEqual(len(audit["source_sha256"]), 64)

    def test_imports_plain_text_fallback(self):
        text = """# Reddit post

        This copied post contains enough substantive first-person information to be retained for citation review. It describes the reported experience in several complete sentences and preserves the text visible on the cited page.
        """
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capture.txt"
            source.write_text(text, encoding="utf-8")
            page, audit = import_one("https://www.reddit.com/example", source, root / "cache")
        self.assertEqual(page["retrieval_status"], "success")
        self.assertEqual(page["access_method"], "manual_text_import")
        self.assertEqual(audit["chunk_count"], 1)

    def test_extracts_reddit_post_and_rendered_comments(self):
        html = """<html><body><shreddit-post post-title="Example thread">
        <div property="schema:articleBody"><p>The original post asks a detailed question about a medical experience and provides enough context for readers to understand what happened.</p></div>
        </shreddit-post>
        <template><shreddit-comment author="reader_one"><div slot="comment"><p>The first rendered comment describes a heavy wave of exhaustion and a strong urge to sleep that is difficult to resist.</p></div></shreddit-comment>
        <shreddit-comment author="reader_two"><div slot="comment"><p>The second rendered comment provides a different personal description of the same experience.</p></div></shreddit-comment></template>
        </body></html>"""
        title, blocks = extract_reddit_html_blocks(html)
        self.assertEqual(title, "Example thread")
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[1]["heading"], "Comment by reader_one")
        self.assertIn("heavy wave of exhaustion", blocks[1]["text"])


if __name__ == "__main__":
    unittest.main()
