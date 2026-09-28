import unittest

from retrieve_citation_evidence import (
    extract_blocks,
    extract_markdown_blocks,
    is_challenge_page,
    make_chunks,
    quality_checked_chunks,
    retrieve_top_chunks,
    sanitize_captured_html,
)


class CitationEvidenceRetrievalTests(unittest.TestCase):
    def test_extracts_main_content_and_drops_navigation(self):
        html = """
        <html><head><title>Example condition</title></head><body>
          <nav><p>Subscribe to our newsletter and browse the menu.</p></nav>
          <main><h1>Symptoms</h1>
            <p>The condition commonly causes persistent burning pain in the affected area.</p>
            <p>Symptoms may worsen after prolonged pressure or physical activity.</p>
          </main>
        </body></html>
        """
        title, blocks = extract_blocks(html)
        self.assertEqual(title, "Example condition")
        self.assertEqual(len(blocks), 2)
        self.assertTrue(all(block["heading"] == "Symptoms" for block in blocks))

    def test_chunks_and_retrieves_relevant_passage(self):
        blocks = [
            {"heading": "Symptoms", "text": "Vulvodynia can cause burning and stinging pain."},
            {"heading": "Treatment", "text": "Treatment may include pelvic floor therapy."},
        ]
        chunks = make_chunks(blocks, target_words=20, max_words=30)
        results = retrieve_top_chunks(
            "Vulvodynia may feel like burning or stinging pain.",
            "What does vulvodynia feel like?",
            chunks,
            top_k=2,
        )
        self.assertEqual(results[0]["heading"], "Symptoms")
        self.assertGreater(results[0]["retrieval_score"], results[1]["retrieval_score"])

    def test_extracts_reader_markdown(self):
        markdown = """Title: Example page
URL Source: https://example.org/page

Markdown Content:
# Overview

This is a sufficiently long introductory paragraph about the condition.

## Symptoms

The condition can cause persistent burning and stinging pain.
"""
        title, blocks = extract_markdown_blocks(markdown)
        self.assertEqual(title, "Example page")
        self.assertEqual([block["heading"] for block in blocks], ["Overview", "Symptoms"])

    def test_detects_security_challenge_as_non_content(self):
        blocks = [
            {
                "heading": "Performing security verification",
                "text": "This website verifies you are not a bot before allowing access.",
            }
        ]
        self.assertTrue(is_challenge_page("Security check", blocks))

    def test_rejects_reference_fragments_and_deduplicates(self):
        blocks = [
            {"heading": "References", "text": "a [...] incomplete reference fragment " * 5},
            {"heading": "Citations", "text": "A long list of citing articles without source text."},
        ]
        chunks, error = quality_checked_chunks(blocks)
        self.assertEqual(chunks, [])
        self.assertIsNotNone(error)

    def test_sanitizes_embedded_source_maps(self):
        html = """<html><head><style>/*# sourceMappingURL=data:application/json;base64,abc */</style></head>
        <body><main><p>This is useful article text that should remain after presentation assets are removed.</p></main>
        <script>window.payload = 'large';</script></body></html>"""
        cleaned = sanitize_captured_html(html)
        self.assertNotIn("sourceMappingURL", cleaned)
        self.assertNotIn("window.payload", cleaned)
        self.assertIn("useful article text", cleaned)

    def test_extracts_div_role_paragraph_article_body(self):
        html = """<html><body><main><section property="articleBody">
        <h2>Causes</h2><div role="paragraph">Fungal infections can cause severe sepsis in susceptible patients, although bacterial infections are more common and account for most cases. The source explains that infections from several organism types may precipitate the syndrome and that their observed frequencies differ in intensive care populations.</div>
        </section><section><h2>References</h2><p>Reference list text that should not replace the article body.</p></section>
        </main></body></html>"""
        _, blocks = extract_blocks(html)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["heading"], "Causes")
        self.assertIn("Fungal infections", blocks[0]["text"])

    def test_falls_back_to_body_when_article_tags_are_tiny(self):
        html = """<html><body><article><p>Small card text only.</p></article>
        <div><h2>Main explanation</h2><p>This longer explanation contains the actual article content and enough medically relevant words to be selected instead of a tiny unrelated article card displayed elsewhere on the page.</p></div>
        </body></html>"""
        _, blocks = extract_blocks(html)
        self.assertTrue(any("actual article content" in block["text"] for block in blocks))


if __name__ == "__main__":
    unittest.main()
