import unittest

from recover_special_citations import (
    classify_url,
    extract_player_response,
    parse_caption_json,
    pmc_blocks,
    reddit_blocks,
    transcript_entries_to_blocks,
    youtube_video_id,
)


class SpecialCitationRecoveryTests(unittest.TestCase):
    def test_classifies_supported_sources(self):
        self.assertEqual(classify_url("https://www.youtube.com/watch?v=abc"), "youtube")
        self.assertEqual(classify_url("https://www.reddit.com/r/x/comments/abc/title/"), "reddit")
        self.assertEqual(classify_url("https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"), "pmc")

    def test_extracts_youtube_player_and_video_ids(self):
        page = '<script>var ytInitialPlayerResponse = {"videoDetails":{"title":"Example"}};</script>'
        self.assertEqual(extract_player_response(page)["videoDetails"]["title"], "Example")
        self.assertEqual(youtube_video_id("https://www.youtube.com/shorts/abc"), "abc")
        self.assertEqual(youtube_video_id("https://www.youtube.com/watch?v=xyz&t=2"), "xyz")

    def test_parses_caption_events(self):
        payload = {
            "events": [
                {"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "First sentence. "}]},
                {"tStartMs": 1000, "dDurationMs": 1000, "segs": [{"utf8": "Second sentence."}]},
            ]
        }
        blocks = parse_caption_json(payload)
        self.assertEqual(len(blocks), 1)
        self.assertIn("First sentence", blocks[0]["text"])
        from_entries = transcript_entries_to_blocks(
            [{"text": "Caption text", "start": 1.5, "duration": 2.0}]
        )
        self.assertIn("Caption text", from_entries[0]["text"])

    def test_parses_reddit_post_and_comments(self):
        payload = [
            {"data": {"children": [{"data": {"title": "Title", "selftext": "Post body text"}}]}},
            {"data": {"children": [{"data": {"body": "Comment text"}}]}},
        ]
        title, blocks = reddit_blocks(payload)
        self.assertEqual(title, "Title")
        self.assertEqual(len(blocks), 2)

    def test_parses_pmc_xml_body(self):
        xml = """<article><front><article-title>Study</article-title></front><body><sec>
        <title>Results</title><p>Substantive study result text appears in this paragraph.</p>
        </sec></body></article>"""
        title, blocks = pmc_blocks(xml)
        self.assertEqual(title, "Study")
        self.assertEqual(blocks[0]["heading"], "Results")

    def test_uses_pmc_abstract_when_body_unavailable(self):
        xml = """<article><front><article-title>Study</article-title><abstract>
        <p>This abstract contains the available evidence when publisher policy omits the full body.</p>
        </abstract></front></article>"""
        _, blocks = pmc_blocks(xml)
        self.assertEqual(blocks[0]["heading"], "Abstract")


if __name__ == "__main__":
    unittest.main()
