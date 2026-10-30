"""Run with: python -m unittest discover tests"""
import csv
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lifecycle_audit_agent as agent  # noqa: E402

POLICY = json.loads((ROOT / "lifecycle_policy.json").read_text())


class NormaliseTests(unittest.TestCase):
    def test_labels_and_internal_names_are_the_same_stage(self):
        for raw in ["Lead ", "lead", "LEAD"]:
            self.assertEqual(agent.normalise_stage(raw, POLICY), ("lead", None))
        self.assertEqual(agent.normalise_stage("Marketing Qualified Lead", POLICY)[0], "marketingqualifiedlead")

    def test_numeric_custom_option_is_flagged(self):
        stage, rule = agent.normalise_stage("1234567", POLICY)
        self.assertIsNone(stage)
        self.assertEqual(rule["label"], "Disqualified")

    def test_unknown_value_goes_to_review(self):
        stage, rule = agent.normalise_stage("Partner-ish", POLICY)
        self.assertIsNone(stage)
        self.assertEqual(rule["action"], "review")


class SampleRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.summary = agent.run(ROOT / "sample_contacts.csv", ROOT / "sample_stage_history.csv",
                                ROOT / "lifecycle_policy.json", cls.tmp, date(2026, 10, 30))
        cls.by_id = {c.record_id: {f.code for f in c.findings} for c in cls.summary["contacts_obj"]}

    def fixes(self):
        with open(self.tmp / "hubspot_lifecycle_fix_import.csv") as f:
            return {r["Record ID"]: r for r in csv.DictReader(f)}

    def test_open_deal_moves_lead_to_opportunity(self):
        self.assertEqual(self.fixes()["105"]["Lifecycle Stage"], "opportunity")

    def test_custom_value_needs_clear_first(self):
        row = self.fixes()["103"]
        self.assertEqual((row["Lifecycle Stage"], row["Lead Status"], row["Clear stage first"]),
                         ("lead", "UNQUALIFIED", "yes"))

    def test_customer_without_won_deal_is_never_auto_fixed(self):
        self.assertIn("CUSTOMER_WITHOUT_WON_DEAL", self.by_id["104"])
        self.assertNotIn("104", self.fixes())

    def test_backward_move_and_writer_conflict_found(self):
        self.assertIn("BACKWARD_MOVE", self.by_id["108"])
        self.assertIn("WRITER_CONFLICT", self.by_id["101"])

    def test_duplicates_go_to_review(self):
        review = {r["record_id"] for r in self.summary["review"]}
        self.assertTrue({"101", "107"} <= review)

    def test_brief_written(self):
        self.assertTrue((self.tmp / "AUDIT_BRIEF.md").read_text().startswith("# Lifecycle Stage audit"))


if __name__ == "__main__":
    unittest.main()
