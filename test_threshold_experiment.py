"""阈值实验离线测试，不访问数据库或真实模型。"""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import evaluate_rag
from policy_loader import load_policy_documents
from rag_service import RagService, RERANK_THRESHOLD
from policy_citations import render_trusted_answer


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        self.docs = load_policy_documents()
        self.by_id = {p["policy_id"]: p for p in self.docs}

    def service(self, ids, scores, threshold=0.5):
        policies = [self.by_id[i] for i in ids]
        collection = Mock(spec=["query"])
        collection.query.return_value = {"documents": [[p["content"] for p in policies]], "metadatas": [policies], "distances": [[0.1] * len(ids)]}
        reranker = Mock()
        reranker.predict.return_value = scores
        chat = Mock(return_value={"message": {"content": render_trusted_answer("餐饮每天100元", [self.by_id["meal_001"]])}})
        return RagService(collection, reranker, self.docs, chat, rerank_threshold=threshold)

    def test_default_and_actual_filter(self):
        self.assertEqual(RERANK_THRESHOLD, 0.5)
        self.assertEqual(RagService(None, None, []).rerank_threshold, 0.5)
        for threshold, expected in ((0.5, set()), (0.45, {"transport_001"}), (0.4, {"transport_001", "hotel_003"})):
            service = self.service(["hotel_003", "transport_001"], [0.426186, 0.496006], threshold)
            result = service.answer_question("其他城市住宿和交通规定？")
            self.assertEqual({p["policy_id"] for p in result["final_policies"]}, expected)

    def test_unrelated_candidates_not_cited(self):
        for threshold in (0.5, 0.45, 0.4):
            service = self.service(["meal_001", "hotel_001"], [0.998, 0.489239], threshold)
            result = service.answer_question("出差吃饭每天的补助标准是什么？")
            self.assertEqual([p["policy_id"] for p in result["policies"]], ["meal_001"])
            self.assertNotIn("hotel_001", result["answer"])
            service = self.service(["meal_001", "transport_001", "hotel_001"], [0.97, 0.98, 0.56], threshold)
            result = service.answer_question("餐饮补贴及高铁报销规定是什么？")
            self.assertEqual({p["policy_id"] for p in result["policies"]}, {"meal_001", "transport_001"})
            self.assertNotIn("hotel_001", result["answer"])
            service.chat.assert_not_called()

    def test_invalid_parameters(self):
        for value in (True, None, "0.4", -1, 0.6, float("nan")):
            with self.assertRaises(ValueError):
                RagService(None, None, [], rerank_threshold=value)
        for value in ("0.6", "nan", "abc"):
            with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                evaluate_rag.main(["--run", "--test-db-path", "copy", "--rerank-threshold", value])

    def test_experiment_requires_explicit_safe_path(self):
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            evaluate_rag.main(["--rerank-threshold", "0.4"])
        with patch("main.validate_test_database", side_effect=ValueError("unsafe")) as validate:
            with self.assertRaises(ValueError):
                evaluate_rag.build_service("unsafe", 0.4)
        validate.assert_called_once_with("unsafe")

    def test_report_records_actual_threshold_and_unique_files(self):
        service = self.service(["meal_001"], [1.0], 0.45)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "knowledge_base").mkdir()
            (root / "knowledge_base" / "company_policy.txt").write_bytes(b"test snapshot")
            with patch.object(evaluate_rag, "ROOT", root), patch.object(evaluate_rag, "load_cases", return_value=[]), patch.object(evaluate_rag, "build_service", return_value=service) as build, patch("sys.stdout", io.StringIO()):
                for _ in range(2):
                    self.assertEqual(evaluate_rag.main(["--run", "--test-db-path", "copy", "--rerank-threshold", "0.45"]), 0)
            build.assert_called_with("copy", rerank_threshold=0.45)
            reports = list((root / "eval_reports").glob("*.json"))
            self.assertEqual(len(reports), 2)
            for report in reports:
                self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["rerank_threshold"], 0.45)


if __name__ == "__main__":
    unittest.main()
