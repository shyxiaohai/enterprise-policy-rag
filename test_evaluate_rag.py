"""评测器离线测试：只使用固定结果和模拟服务。"""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import evaluate_rag as evaluation


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.cases = evaluation.load_cases()

    def test_dataset(self):
        self.assertEqual(len(self.cases), 25)
        self.assertEqual(len({c["case_id"] for c in self.cases}), 25)

    def test_invalid_dataset(self):
        variants = [[], [self.cases[0], self.cases[0]]]
        for field, value in (("expected_policy_ids", ["unknown"]), ("expected_status", "invalid"), ("expected_facts", "wrong")):
            case = copy.deepcopy(self.cases[0])
            case[field] = value
            variants.append([case])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            for value in variants:
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    evaluation.load_cases(path)

    def test_metrics_denominators(self):
        cases = copy.deepcopy([self.cases[6], self.cases[12]])
        service = Mock()
        service.answer_question.side_effect = [
            {"status": "ok", "answer": "不以字符串判断正确性", "retrieved": [{"policy_id": "hotel_001"}], "final_policies": [{"policy_id": "hotel_001"}, {"policy_id": "meal_001"}]},
            {"status": "no_policy", "answer": "无法确认", "retrieved": [], "final_policies": []}
        ]
        report = evaluation.summarize(evaluation.evaluate_cases(service, cases))
        self.assertEqual(report["recall_at_5_macro"], {"value": 0.5, "denominator": 1})
        self.assertEqual(report["final_all_expected_hit_rate"], {"value": 1.0, "denominator": 1})
        self.assertEqual(report["status_match_rate"], {"value": 1.0, "denominator": 1})
        self.assertEqual(report["average_response_seconds"]["denominator"], 2)
        self.assertIn("未自动计算", report["semantic_accuracy"])

    def test_exception_counts_as_miss(self):
        service = Mock()
        service.answer_question.side_effect = RuntimeError("内部细节")
        rows = evaluation.evaluate_cases(service, self.cases[:1])
        self.assertEqual(rows[0]["recall_at_5"], 0)
        self.assertEqual(rows[0]["status_match"], False)
        self.assertNotIn("内部细节", str(rows))
        self.assertEqual(evaluation.summarize(rows)["request_failures"], 1)

    def test_recall_uses_only_first_five(self):
        service = Mock()
        service.answer_question.return_value = {"status": "ok", "retrieved": [{"policy_id": "meal_001"}] * 5 + [{"policy_id": "hotel_001"}], "final_policies": []}
        self.assertEqual(evaluation.evaluate_cases(service, self.cases[:1])[0]["recall_at_5"], 0)

    def test_default_no_resources(self):
        with patch.object(evaluation, "build_service", side_effect=AssertionError("禁止初始化")), patch("sys.stdout", io.StringIO()):
            self.assertEqual(evaluation.main([]), 0)

    def test_explicit_run_required(self):
        for args in (["--run"], ["--test-db-path", "copy"]):
            with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                evaluation.main(args)

    def test_path_validation_precedes_resource_import(self):
        with patch("main.validate_test_database", side_effect=ValueError("unsafe")) as validate:
            with self.assertRaises(ValueError):
                evaluation.build_service("unsafe")
        validate.assert_called_once_with("unsafe")

    def test_invalid_run_does_not_write_report(self):
        with patch.object(evaluation, "build_service", side_effect=ValueError("unsafe")), patch.object(Path, "mkdir") as mkdir, patch("sys.stderr", io.StringIO()):
            self.assertEqual(evaluation.main(["--run", "--test-db-path", "unsafe"]), 1)
        mkdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
