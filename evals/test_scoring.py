import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "evals"))

from core import decide_score, validate_jd_evidence, validate_schema
from helpers import assessment_for, jd_for, make_profile, ocr_for


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.profile = make_profile()
        self.item = {"source_file": "agent.png", "sha256": "a" * 64, "ocr_path": "/tmp/IMG-001.txt"}
        self.ocr = ocr_for("agent")

    def assessment(self):
        return assessment_for(self.item, self.profile, "agent")

    def test_original_demo_score_constraint_contract(self):
        """独立人工预期对照原 Demo，而非把同一函数调用两次当作双后端证据。"""
        contract = json.loads((ROOT / "evals" / "legacy_contract.json").read_text(encoding="utf-8"))
        for case in contract["cases"]:
            with self.subTest(case=case["name"]):
                assessment = self.assessment()
                assessment["recommendation_score"] = case["model_score"]
                assessment["redline_assessments"][0].update({
                    "conclusion": "true" if case["hard_reject"] else "false",
                    "evidence_quote": "开发Agent应用" if case["hard_reject"] else "",
                    "rationale": "TEST：原 Demo 分数约束契约",
                })
                decision = decide_score(assessment, self.profile, self.ocr)
                self.assertEqual(decision["recommendation_score"], case["expected_score"])
                self.assertEqual(decision["hard_reject"], case["expected_hard_reject"])

    def test_zero_redline_profile_is_valid(self):
        profile = make_profile("test-zero-redlines", rules=())
        assessment = assessment_for(self.item, profile, "agent")
        self.assertEqual(assessment["redline_assessments"], [])
        validate_schema(profile, "profile")
        validate_schema(assessment, "assessment")
        decision = decide_score(assessment, profile, self.ocr)
        self.assertFalse(decision["hard_reject"])
        self.assertEqual(decision["recommendation_score"], 4)

    def test_valid_hard_reject_forces_score_one(self):
        assessment = self.assessment()
        assessment["redline_assessments"][0].update({
            "conclusion": "true", "evidence_quote": "开发Agent应用", "rationale": "TEST：模拟明确证据",
        })
        assessment["recommendation_score"] = 5
        decision = decide_score(assessment, self.profile, self.ocr)
        self.assertTrue(decision["hard_reject"])
        self.assertEqual(decision["recommendation_score"], 1)

    def test_model_score_one_without_hard_reject_becomes_two(self):
        assessment = self.assessment()
        assessment["recommendation_score"] = 1
        decision = decide_score(assessment, self.profile, self.ocr)
        self.assertFalse(decision["hard_reject"])
        self.assertEqual(decision["recommendation_score"], 2)

    def test_true_redline_without_evidence_is_rejected(self):
        assessment = self.assessment()
        assessment["redline_assessments"][0].update({"conclusion": "true", "evidence_quote": ""})
        with self.assertRaisesRegex(ValueError, "缺少 JD 原文证据"):
            decide_score(assessment, self.profile, self.ocr)

    def test_fabricated_evidence_is_rejected(self):
        assessment = self.assessment()
        assessment["matched_points"][0]["evidence_quote"] = "原文不存在的内容"
        with self.assertRaisesRegex(ValueError, "不是 OCR 原文子串"):
            decide_score(assessment, self.profile, self.ocr)

    def test_unknown_redline_does_not_reject_and_becomes_pending(self):
        decision = decide_score(self.assessment(), self.profile, self.ocr)
        self.assertFalse(decision["hard_reject"])
        self.assertTrue(any("外包" in value for value in decision["pending_conditions"]))

    def test_missing_rule_assessment_is_rejected(self):
        assessment = self.assessment()
        assessment["redline_assessments"].pop()
        with self.assertRaisesRegex(ValueError, "画像规则不一致"):
            decide_score(assessment, self.profile, self.ocr)

    def test_unknown_jd_field_must_not_have_fabricated_evidence(self):
        jd = jd_for(self.item, "agent")
        jd["salary_range"] = "未提及"
        jd["field_evidence"]["salary_range"] = []
        validate_jd_evidence(jd, self.ocr)
        jd["field_evidence"]["salary_range"] = ["20-30K"]
        with self.assertRaisesRegex(ValueError, "不应伪造证据"):
            validate_jd_evidence(jd, self.ocr)

    def test_assessment_schema(self):
        validate_schema(self.assessment(), "assessment")
        validate_schema(jd_for(self.item, "agent"), "jd")


if __name__ == "__main__":
    unittest.main()
