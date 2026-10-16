"""Deterministic business protections, with simulated retrieval and generation."""
import unittest
from test_rag_service import RagServiceTests
from policy_citations import render_trusted_answer
from policy_evidence_guard import scope_question
from rag_service import get_city_level, check_required_conditions, filter_policy_by_condition, RERANK_THRESHOLD


class BusinessSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RagServiceTests()
        self.fixture.setUp()

    def answer(self, question, ids):
        return self.fixture.service(ids).answer_question(question)

    def test_meal_amount_true_citation_cannot_authorize_inference(self):
        self.fixture.chat.side_effect = None
        self.fixture.chat.return_value = {'message': {'content': render_trusted_answer(
            '超过100元的部分一定不能报销。', [self.fixture.by_id['meal_001']])}}
        for question in ('餐饮花了180元，超过100元能报吗？', '餐费补贴的上限和报销比例是多少？',
                         '出差餐饮标准是多少，超出部分能不能报？', '餐饮补贴差额可以领取吗？'):
            result = self.answer(question, ['meal_001'])
            self.assertEqual(result['status'], 'guarded')
            self.assertIn('无法确认', result['answer'])
            self.assertIn(self.fixture.by_id['meal_001']['content'], result['answer'])
            self.assertNotIn('一定不能报销', result['answer'])
        self.fixture.chat.assert_not_called()

    def test_explicit_exclusions_remove_conditions_and_final_citations(self):
        for question, expected in (
            ('不问住宿，只问餐饮补贴。', {'meal_001'}),
            ('只查交通规定，不查餐饮补贴。', {'transport_001'}),
            ('不查交通，只问餐饮标准。', {'meal_001'})):
            result = self.answer(question, ['hotel_001', 'meal_001', 'transport_001'])
            self.assertNotEqual(result['status'], 'needs_input')
            for key in ('policies', 'final_policies'):
                self.assertEqual({p['policy_id'] for p in result[key]}, expected)
            self.assertNotIn('hotel_001', result['answer'])
        self.fixture.chat.assert_not_called()

    def test_ambiguous_exclusion_is_not_guessed(self):
        result = self.answer('不问住宿和餐饮是否有额外条件，只查标准。', ['meal_001'])
        self.assertEqual(result['status'], 'guarded')
        self.assertIn('作用范围', result['answer'])
        self.fixture.chat.assert_not_called()

    def test_multi_city_missing_evidence_never_complete_or_invented(self):
        result = self.answer('一线和二线城市住宿日标准分别说明。', ['hotel_002'])
        self.assertEqual(result['status'], 'guarded')
        self.assertIn('证据可能不完整', result['answer'])
        self.assertNotIn('500', result['answer'])
        self.assertNotIn('X元', result['answer'])
        self.assertIn('不代表知识库不存在', result['answer'])
        self.fixture.chat.assert_not_called()

    def test_multi_city_retains_both_available_originals(self):
        result = self.answer('一线城市和二线城市住宿标准。', ['hotel_001', 'hotel_002'])
        self.assertEqual({p['policy_id'] for p in result['policies']}, {'hotel_001', 'hotel_002'})
        for pid in ('hotel_001', 'hotel_002'):
            self.assertIn(self.fixture.by_id[pid]['content'], result['answer'])

    def test_frozen_single_city_contract_and_missing_city(self):
        for question, level, pid in (
            ('一线城市住宿标准', 'tier_1', 'hotel_001'),
            ('二线城市住宿标准', 'tier_2', 'hotel_002'),
            ('其他城市住宿标准', 'other', 'hotel_003')):
            self.assertEqual(get_city_level(question), level)
            self.assertTrue(check_required_conditions(question)[0])
            filtered = filter_policy_by_condition(question, self.fixture.documents)
            self.assertEqual({p['policy_id'] for p in filtered if p['policy_id'].startswith('hotel_')}, {pid})
        self.assertIsNone(get_city_level('住宿标准'))
        self.assertFalse(check_required_conditions('住宿标准')[0])
        self.assertEqual(RERANK_THRESHOLD, 0.50)

    def test_multi_city_retrieval_does_not_apply_single_city_where(self):
        for question in ('一线城市和二线城市住宿标准', '一线和二线城市住宿标准'):
            service = self.fixture.service(['hotel_001', 'hotel_002'])
            result = service.answer_question(question)
            self.assertNotIn('where', service.collection.calls[0])
            self.assertEqual({p['policy_id'] for p in result['final_policies']}, {'hotel_001', 'hotel_002'})
            self.assertEqual(result['status'], 'guarded')
        self.fixture.chat.assert_not_called()

    def test_hotel_amount_guard_keeps_priority_for_partial_meal_standard(self):
        result = self.answer('二线城市住宿花了600元能报多少，餐饮每天补贴多少？', ['hotel_002'])
        self.assertIn('未明确实际报销金额的计算规则', result['answer'])
        self.assertNotIn('meal_001', result['answer'])
        self.assertEqual(result['status'], 'guarded')
        self.fixture.chat.assert_not_called()

    def test_business_negation_preserved_and_approval_guarded(self):
        question = '未经批准可以报销商务座吗？'
        self.assertEqual(scope_question(question), (question, False))
        result = self.answer(question, ['transport_001'])
        self.assertEqual(result['status'], 'ok')
        for question in ('商务座没有拿到批准，能报销吗？', '商务座批准后能全额报销吗？'):
            result = self.answer(question, ['transport_001'])
            self.assertEqual(result['status'], 'guarded')
            self.assertIn('未经批准不得报销', result['answer'])

    def test_non_trip_does_not_become_standard_entitlement(self):
        result = self.answer('没有出差，在办公室吃午饭能领100元吗？', ['meal_001'])
        self.assertEqual(result['status'], 'guarded')
        self.assertIn('不足以确认非出差', result['answer'])
        self.fixture.chat.assert_not_called()

    def test_fake_meal_original_still_discards_model_body(self):
        self.fixture.chat.side_effect = None
        self.fixture.chat.return_value = {'message': {'content': render_trusted_answer(
            '危险模型正文', [dict(self.fixture.by_id['meal_001'], content='出差每天补贴X元。')])}}
        result = self.answer('出差每天餐饮补贴多少？', ['meal_001'])
        self.assertNotIn('危险模型正文', result['answer'])
        self.assertNotIn('X元', result['answer'])
        self.assertTrue(result['diagnostics']['model_answer_discarded'])


if __name__ == '__main__':
    unittest.main()
