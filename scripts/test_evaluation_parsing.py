#!/usr/bin/env python3
"""Tests for AIO structure parsing and claim enrichment."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import TypeAdapter

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from extract_medical_claims import ClaimExtraction, validate_and_enrich_claims  # noqa: E402
from parse_aio import parse_record  # noqa: E402


class ParsingTests(unittest.TestCase):
    def test_parse_blocks_and_local_citations(self) -> None:
        record = {
            "dataset_id": "TEST_1",
            "sample_order": 1,
            "run": 1,
            "question": "What is a TIA?",
            "ai_overview_present": True,
            "citations": [
                {
                    "position": 1,
                    "title": "Example Health",
                    "url": "https://example.org/tia",
                    "domain": "example.org",
                }
            ],
            "aio_html_sanitized": """
              <div data-container-id="main-col">
                <div class="n6owBd awi2gc">A TIA is temporary.
                  <span class="WBgIic"><a class="PMDqCb" href="https://example.org/tia"></a>
                    <button class="vDOt8c" aria-label="Example Health (+1) - Related results">Example Health +1</button>
                  </span>
                </div>
                <div class="otQkpb" role="heading">Symptoms</div>
                <ul><li><span class="iNqyIf">Symptoms can include weakness.</span></li></ul>
              </div>
              <div data-container-id="rhs-col">Source-card text that must be excluded.</div>
            """,
        }
        parsed = parse_record(record)
        self.assertEqual(parsed["parse_status"], "success")
        self.assertEqual([block["block_type"] for block in parsed["blocks"]], ["paragraph", "heading", "list_item"])
        self.assertEqual(parsed["blocks"][0]["text"], "A TIA is temporary.")
        self.assertEqual(parsed["blocks"][0]["local_citation_ids"], ["src_001"])
        self.assertNotIn("Source-card", parsed["main_text"])
        self.assertEqual(parsed["blocks"][0]["additional_sources_indicated"], 1)

    def test_absent_aio(self) -> None:
        parsed = parse_record(
            {
                "dataset_id": "TEST_2",
                "sample_order": 2,
                "run": 1,
                "question": "Question",
                "ai_overview_present": False,
            }
        )
        self.assertEqual(parsed["parse_status"], "not_present")
        self.assertEqual(parsed["blocks"], [])

    def test_claims_inherit_block_citations(self) -> None:
        record = {
            "blocks": [
                {
                    "block_id": "b001",
                    "text": "A TIA is temporary.",
                    "local_citation_ids": ["src_001"],
                    "candidate_citation_ids": ["src_001"],
                    "citation_scope": "local",
                    "unresolved_citation_marker_count": 0,
                    "additional_sources_indicated": 0,
                }
            ]
        }
        extraction = TypeAdapter(ClaimExtraction).validate_python(
            {
                "claims": [
                    {
                        "block_id": "b001",
                        "source_quote": "A TIA is temporary.",
                        "claim_text": "A transient ischemic attack is temporary.",
                    }
                ]
            }
        )
        claims, warnings = validate_and_enrich_claims(record, extraction)
        self.assertEqual(warnings, [])
        self.assertEqual(claims[0]["local_citation_ids"], ["src_001"])
        self.assertEqual(claims[0]["candidate_citation_ids"], ["src_001"])
        self.assertTrue(claims[0]["source_quote_validated"])


if __name__ == "__main__":
    unittest.main()
