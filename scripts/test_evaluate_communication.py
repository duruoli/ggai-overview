"""Checks for the deterministic parts of communication evaluation."""

import unittest

from evaluate_communication import (
    ACTIONABILITY_IDS,
    ITEM_IDS,
    UNDERSTANDABILITY_IDS,
    CommunicationJudgment,
    score_items,
    validate_judgment,
)


def judgment(*, applicable=True, **labels):
    payload = {
        "actionability_applicable": applicable,
        "applicability_reason": "The question does or does not call for action.",
    }
    for item_id in ITEM_IDS:
        payload[item_id] = {
            "label": labels.get(item_id, "yes" if applicable or item_id.startswith("u") else "na"),
            "reason": "Reason",
            "quote": "",
        }
    return CommunicationJudgment.model_validate(payload)


class CommunicationEvaluationTests(unittest.TestCase):
    def test_na_items_are_excluded_from_each_denominator(self):
        result = judgment(u3="na", a3="na", a2="no")
        self.assertEqual(validate_judgment(result, "An answer"), [])
        items = {item_id: getattr(result, item_id).model_dump(mode="json") for item_id in ITEM_IDS}
        self.assertEqual(score_items(items, UNDERSTANDABILITY_IDS), 100.0)
        self.assertEqual(score_items(items, ACTIONABILITY_IDS), 50.0)

    def test_inapplicable_actionability_has_no_numeric_score(self):
        result = judgment(applicable=False)
        self.assertEqual(validate_judgment(result, "An answer"), [])
        items = {item_id: getattr(result, item_id).model_dump(mode="json") for item_id in ITEM_IDS}
        self.assertIsNone(score_items(items, ACTIONABILITY_IDS))

    def test_invalid_na_and_fabricated_quote_are_rejected(self):
        result = judgment(applicable=False, a1="yes", u1="na")
        result.u2.quote = "Invented text"
        warnings = validate_judgment(result, "An answer")
        self.assertTrue(any("u1 cannot be na" in warning for warning in warnings))
        self.assertTrue(any("a1 must be na" in warning for warning in warnings))
        self.assertTrue(any("u2 quote" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
