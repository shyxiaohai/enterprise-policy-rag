"""仅用标准库和模拟资源验证服务；不导入主程序。"""
import ast
import builtins
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from policy_loader import load_policy_documents
from rag_service import RagService, build_prompt
from policy_citations import applicable_policies, render_trusted_answer
from rag_service import get_city_level


class FakeCollection:
    def __init__(self, policies):
        self.policies = policies
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        policies = self.policies
        if "where" in kwargs:
            level = kwargs["where"]["$or"][0]["city_level"]
            policies = [p for p in policies if p.get("city_level", "") in (level, "")]
        policies = policies[:kwargs["n_results"]]
        return {
            "documents": [[p["content"] for p in policies]],
            "metadatas": [[{"source": p["source"], "policy_id": p["policy_id"]} for p in policies]],
            "distances": [[0.1 + i * 0.1 for i in range(len(policies))]]
        }

    def __getattr__(self, name):
        if name in ("add", "update", "upsert", "delete", "reset"):
            raise AssertionError("禁止写数据库")
        raise AttributeError(name)


class RagServiceTests(unittest.TestCase):
    def setUp(self):
        self.documents = load_policy_documents()
        self.by_id = {p["policy_id"]: p for p in self.documents}
        def valid_chat(**kwargs):
            prompt = kwargs["messages"][0]["content"]
            question = prompt.split("【用户问题】")[1].split("请按照以下格式回答")[0].strip()
            policies = [p for p in self.documents if "政策编号：" + p["policy_id"] + "\n" in prompt]
            relevant = applicable_policies(question, policies, self.documents, get_city_level(question))
            return {"message": {"content": render_trusted_answer("模拟回答", relevant)}}
        self.chat = Mock(side_effect=valid_chat)

    def service(self, ids, scores=None):
        collection = FakeCollection([self.by_id[i] for i in ids])
        reranker = Mock()
        reranker.predict.side_effect = lambda pairs: scores if scores is not None else [1.0] * len(pairs)
        return RagService(collection, reranker, self.documents, self.chat)

    def test_standard_query_and_model_parameters(self):
        service = self.service(["hotel_001"])
        result = service.answer_question("一线城市住宿标准是多少？")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["answer"], render_trusted_answer("模拟回答", result["policies"]))
        self.assertEqual(result["policies"][0]["policy_id"], "hotel_001")
        self.assertEqual(service.collection.calls[0], {
            "query_texts": ["一线城市住宿标准是多少？"], "n_results": 5,
            "where": {"$or": [{"city_level": "tier_1"}, {"city_level": ""}]}
        })
        kwargs = self.chat.call_args.kwargs
        self.assertEqual(kwargs["model"], "qwen2.5:3b")
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": build_prompt("一线城市住宿标准是多少？", result["policies"])}])

    def test_joint_query_and_distinct_debug_snapshots(self):
        service = self.service(["hotel_001", "meal_001"], [0.6, 0.9])
        result = service.answer_question("一线城市住宿和餐饮补贴标准分别是多少？")
        self.assertEqual([p["policy_id"] for p in result["retrieved"]], ["hotel_001", "meal_001"])
        self.assertEqual([p["policy_id"] for p in result["reranked"]], ["meal_001", "hotel_001"])
        self.assertNotIn("rerank_score", result["retrieved"][0])
        result["final_policies"][0]["content"] = "修改快照"
        self.assertNotEqual(result["reranked"][0]["content"], "修改快照")
        self.assertNotEqual(result["policies"][0]["content"], "修改快照")

    def test_no_city_filter_for_meal(self):
        service = self.service(["meal_001"])
        self.assertEqual(service.answer_question("每天餐饮补贴是多少？")["status"], "ok")
        self.assertNotIn("where", service.collection.calls[0])

    def test_missing_city(self):
        result = self.service(["hotel_001"]).answer_question("住宿标准是多少？")
        self.assertEqual(result["status"], "needs_input")
        self.assertIn("请提供出差城市", result["message"])
        self.chat.assert_not_called()

    def test_empty_retrieval_and_threshold(self):
        for ids, scores in (([], None), (["hotel_001"], [0.49])):
            result = self.service(ids, scores).answer_question("公司有没有健身补贴？")
            self.assertEqual(result["status"], "no_policy")
            self.assertEqual(result["policies"], [])
        self.chat.assert_not_called()

    def test_threshold_boundary(self):
        self.assertEqual(self.service(["meal_001"], [0.5]).answer_question("餐饮补贴标准？")["status"], "ok")

    def test_hotel_amount_guard(self):
        for question in (
            "一线城市住宿花350元可以报500元吗？",
            "一线城市住宿花650元能报多少？",
            "一线城市住宿可以全额报销吗？",
            "一线城市住宿最高报销金额是多少？",
            "一线城市住宿没花满标准，差额能领吗？"
        ):
            with self.subTest(question=question):
                result = self.service(["hotel_001"]).answer_question(question)
                self.assertEqual(result["status"], "guarded")
                self.assertIn("未明确实际报销金额的计算规则", result["answer"])
                self.assertIn("hotel_001", result["answer"])
        self.chat.assert_not_called()

    def test_guard_joint_question_and_missing_evidence(self):
        question = "一线城市住宿花650元能报多少，餐饮补贴每天多少？"
        result = self.service(["hotel_001", "meal_001"]).answer_question(question)
        self.assertEqual(result["status"], "guarded")
        self.assertIn(self.by_id["meal_001"]["content"], result["answer"])
        self.assertIn("meal_001", result["answer"])
        result = self.service(["hotel_001"]).answer_question(question)
        self.assertEqual(result["status"], "guarded")
        self.assertIn(self.by_id["hotel_001"]["content"], result["answer"])
        self.assertIn("未明确实际报销金额的计算规则", result["answer"])
        self.assertIn("餐饮：", result["answer"])
        self.assertIn("依据不足", result["answer"])
        self.assertIn("无法确认", result["answer"])
        self.assertEqual([p["policy_id"] for p in result["policies"]], ["hotel_001"])
        self.assertNotIn("meal_001", result["answer"])
        self.assertNotIn(self.by_id["meal_001"]["content"], result["answer"])
        result = self.service(["meal_001"]).answer_question(question)
        self.assertEqual(result["status"], "guarded")
        self.assertIn(self.by_id["meal_001"]["content"], result["answer"])
        self.assertIn("没有对应城市等级的住宿依据", result["answer"])
        self.assertNotIn("500", result["answer"])
        self.assertEqual([p["policy_id"] for p in result["policies"]], ["meal_001"])
        self.assertNotIn("hotel_001", result["answer"])
        self.chat.assert_not_called()

    def test_city_condition_filter(self):
        service = self.service(["hotel_002", "meal_001"])
        # 模拟异常召回，确认服务仍执行后续城市条件过滤。
        service.collection.query = Mock(return_value={
            "documents": [[self.by_id["hotel_002"]["content"], self.by_id["meal_001"]["content"]]],
            "metadatas": [[self.by_id["hotel_002"], self.by_id["meal_001"]]],
            "distances": [[0.1, 0.2]]
        })
        result = service.answer_question("一线城市住宿和餐饮标准？")
        self.assertEqual([p["policy_id"] for p in result["final_policies"]], ["meal_001"])

    def test_model_failure_then_success(self):
        self.chat.side_effect = [RuntimeError("不要显示的内部错误"), {"message": {"content": render_trusted_answer("恢复回答", [self.by_id["meal_001"]])}}]
        service = self.service(["meal_001"])
        result = service.answer_question("餐饮标准？")
        self.assertEqual(result["status"], "error")
        self.assertIn("模型调用失败", result["message"])
        self.assertNotIn("内部错误", result["message"])
        self.assertEqual(service.answer_question("餐饮标准？")["answer"], render_trusted_answer("恢复回答", [self.by_id["meal_001"]]))

    def test_rerank_does_not_mutate_input(self):
        service = self.service(["meal_001"])
        candidates = service.retrieve_policy("餐饮标准？")
        before = deepcopy(candidates)
        service.rerank_policy("餐饮标准？", candidates)
        self.assertEqual(candidates, before)

    def test_prompt_and_business_functions_match_frozen_baseline(self):
        # 仅解析源代码并执行选中的函数定义，不导入或执行主程序。
        path = Path(__file__).resolve().parent / "rag_baseline_fixture.py"
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        namespace = {}
        exec(compile(ast.Module(body=[functions["build_prompt"]], type_ignores=[]), str(path), "exec"), namespace)
        self.assertEqual(build_prompt("测试问题", self.documents), namespace["build_prompt"]("测试问题", self.documents))
        service_tree = ast.parse((path.parent / "rag_service.py").read_text(encoding="utf-8"))
        service_functions = {node.name: node for node in service_tree.body if isinstance(node, ast.FunctionDef)}
        for name in ("get_city_level", "check_required_conditions", "filter_policy_by_condition", "build_reimbursement_guard_answer"):
            self.assertEqual(
                ast.dump(ast.Module(body=functions[name].body, type_ignores=[])),
                ast.dump(ast.Module(body=service_functions[name].body, type_ignores=[]))
            )

    def test_import_has_no_side_effects(self):
        path = Path(__file__).resolve().parent / "rag_service.py"
        spec = importlib.util.spec_from_file_location("isolated_rag_service", path)
        module = importlib.util.module_from_spec(spec)
        original_import = builtins.__import__

        def restricted_import(name, *args, **kwargs):
            if name in ("main", "chromadb", "ollama", "sentence_transformers", "policy_loader"):
                raise AssertionError("导入时访问外部资源：" + name)
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=restricted_import), patch("builtins.input", side_effect=AssertionError("禁止输入")), patch("builtins.open", side_effect=AssertionError("禁止读取文件")):
            spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "RagService"))


if __name__ == "__main__":
    unittest.main()
