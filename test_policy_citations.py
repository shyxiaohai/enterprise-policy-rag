"""确定性引用测试，不调用模型。"""
import unittest
from unittest.mock import Mock
from policy_loader import load_policy_documents
from policy_citations import CitationError, validate_and_render, render_trusted_answer
from rag_service import RagService


class CitationTests(unittest.TestCase):
    def setUp(self):
        self.docs = load_policy_documents()
        self.meal = next(p for p in self.docs if p["policy_id"] == "meal_001")
        self.hotel = next(p for p in self.docs if p["policy_id"] == "hotel_001")

    def test_valid_complete_original(self):
        answer, used = validate_and_render(render_trusted_answer("回答", [self.meal]), [self.meal], [self.meal])
        self.assertIn(self.meal["content"], answer)
        self.assertEqual(used, [self.meal])

    def test_invalid_citations(self):
        valid = render_trusted_answer("回答", [self.meal])
        invalid = [valid.replace(self.meal["content"], "报销标准为X元/天"),
                   valid.replace("meal_001", "stay_001"), valid.replace("company_policy.txt", "福利.txt"),
                   render_trusted_answer("回答", [self.hotel]), "没有引用格式",
                   valid.replace(self.meal["content"], "每天餐饮补贴标准为100元。")]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(CitationError):
                validate_and_render(text, [self.meal], [self.meal])

    def test_true_but_unrelated(self):
        with self.assertRaisesRegex(CitationError, "人工复核"):
            validate_and_render(render_trusted_answer("回答", [self.meal]), [self.meal, self.hotel], [self.hotel])

    def test_legal_citation_not_semantic_validation(self):
        answer, _ = validate_and_render(render_trusted_answer("每天999元。", [self.meal]), [self.meal], [self.meal])
        self.assertIn("999元", answer)  # 明确记录边界：引用检查不能证明结论正确。

    def test_full_original_skips_model(self):
        policy = next(p for p in self.docs if p["policy_id"] == "leave_002")
        collection, reranker, chat = Mock(), Mock(), Mock()
        collection.query.return_value = {"documents": [[policy["content"]]], "metadatas": [[policy]], "distances": [[0.1]]}
        reranker.predict.return_value = [1.0]
        result = RagService(collection, reranker, self.docs, chat).answer_question("工作满3年的年假政策原文是什么？")
        self.assertEqual(result["status"], "ok")
        self.assertIn(policy["content"], result["answer"])
        chat.assert_not_called()

    def test_fake_original_safe_failure(self):
        collection, reranker, chat = Mock(), Mock(), Mock()
        collection.query.return_value = {"documents": [[self.meal["content"]]], "metadatas": [[self.meal]], "distances": [[0.1]]}
        reranker.predict.return_value = [1.0]
        chat.return_value = {"message": {"content": render_trusted_answer("错误结论", [dict(self.meal, content="X元/天")])}}
        result = RagService(collection, reranker, self.docs, chat).answer_question("餐饮补贴标准是多少？")
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("错误结论", result["answer"])
        self.assertIn(self.meal["content"], result["answer"])
        self.assertEqual(result["policies"][0]["policy_id"], "meal_001")
        self.assertEqual(result["diagnostics"]["citation_reason_code"], "original_mismatch")

    def test_reason_codes_preserve_strict_rejection(self):
        valid = render_trusted_answer("回答", [self.meal])
        cases = [(valid.replace("meal_001", "stay_001"), "unknown_or_out_of_context_id"),
                 (valid.replace("company_policy.txt", "other.txt"), "source_mismatch"),
                 (valid.replace(self.meal["content"], "X元/天"), "original_mismatch"),
                 ("回答", "unsupported_format"),
                 ("【回答】\n回答\n【政策依据】\n", "no_verifiable_citation")]
        for answer, code in cases:
            with self.subTest(code=code), self.assertRaises(CitationError) as caught:
                validate_and_render(answer, [self.meal], [self.meal])
            self.assertEqual(caught.exception.reason_code, code)

    def test_proven_simple_fallback_selects_only_needed_policies(self):
        from policy_citations import safe_original_fallback
        cases = [("工作满1年每年有几天带薪年假？", None, {"leave_001"}),
                 ("一线城市住宿和餐饮补贴标准分别多少？", "tier_1", {"hotel_001", "meal_001"}),
                 ("一线城市餐饮补贴和住宿标准分别多少？", "tier_1", {"hotel_001", "meal_001"}),
                 ("未经批准可以报销商务座吗？", None, {"transport_001"}),
                 ("在一线城市出差住酒店，公司的住宿标准是每天多少钱？", "tier_1", {"hotel_001"})]
        for question, city, expected in cases:
            with self.subTest(question=question):
                answer, used = safe_original_fallback(question, self.docs, self.docs, city)
                self.assertEqual({p["policy_id"] for p in used}, expected)
                for policy in used:
                    self.assertIn(policy["content"], answer)

    def test_fallback_never_infers_complex_or_missing_conditions(self):
        from policy_citations import safe_original_fallback
        for question in ("住宿标准是多少？", "一线城市住宿花350元能报多少？", "批准后商务座可以全额报销吗？", "满4年年假多少？", "工作满1年有几天年假以及能否折现？", "公司健身补贴多少？"):
            self.assertIsNone(safe_original_fallback(question, self.docs, self.docs, "tier_1"))
        self.assertIsNone(safe_original_fallback("一线城市住宿和餐饮补贴分别多少？", [self.meal], self.docs, "tier_1"))

    def test_format_failure_recovered_without_model_body(self):
        collection, reranker, chat = Mock(), Mock(), Mock()
        collection.query.return_value = {"documents": [[self.hotel["content"], self.meal["content"]]], "metadatas": [[self.hotel, self.meal]], "distances": [[0.1, 0.2]]}
        reranker.predict.return_value = [1.0, 1.0]
        chat.return_value = {"message": {"content": "错误结论；政策编号：hotel_001，来源文件：company_policy.txt"}}
        result = RagService(collection, reranker, self.docs, chat).answer_question("一线城市住宿标准是多少？")
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("错误结论", result["answer"])
        self.assertEqual([p["policy_id"] for p in result["policies"]], ["hotel_001"])
        self.assertEqual(result["diagnostics"]["citation_reason_code"], "unsupported_format")
