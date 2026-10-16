"""Offline tests for narrowly scoped original-policy recovery."""
import unittest
import test_rag_service as fixtures
from policy_citations import safe_original_fallback, render_trusted_answer
from policy_evidence_guard import meal_risks


class OriginalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RagServiceTests()
        self.fixture.setUp()
        self.fixture.chat.side_effect = None
        self.fixture.chat.return_value = {'message': {'content': 'discard this model body'}}

    def test_simple_paraphrases_recover_only_requested_policy(self):
        for question, pid in (
            ('目的地属于其他城市，旅馆住宿每天的标准是多少？', 'hotel_003'),
            ('每天的饭钱补贴，出差期间按多少算？', 'meal_001'),
            ('二线城市出差餐饮每天补贴多少？', 'meal_001'),
            ('去其他城市出差吃饭每天补助多少？', 'meal_001'),
            ('工作刚好满一年，每年带薪年假有多少天？', 'leave_001')):
            result = self.fixture.service([pid, 'transport_001', 'leave_002']).answer_question(question)
            self.assertEqual(result['status'], 'ok', question)
            self.assertEqual([p['policy_id'] for p in result['policies']], [pid])
            self.assertIn(self.fixture.by_id[pid]['content'], result['answer'])
            self.assertNotIn('discard this', result['answer'])
            self.assertTrue(result['diagnostics']['model_answer_discarded'])

    def test_joint_recovery_needs_both_final_policies(self):
        question = '其他城市出差，住宿日标准和饭钱日补贴各是多少？'
        result = self.fixture.service(['hotel_003', 'meal_001', 'transport_001']).answer_question(question)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual({p['policy_id'] for p in result['policies']}, {'hotel_003', 'meal_001'})
        result = self.fixture.service(['hotel_003', 'meal_001'], [1.0, 0.49]).answer_question(question)
        self.assertEqual(result['status'], 'guarded')
        self.assertNotIn('meal_001', result['answer'])

    def test_false_original_and_source_cannot_be_used_by_fallback(self):
        policy = self.fixture.by_id['meal_001']
        for changes in ({'content': '餐饮补贴X元/天。'}, {'source': 'invented.txt'}, {'policy_id': 'meal_999'}):
            self.assertIsNone(safe_original_fallback('出差餐饮补贴多少？',
                [dict(policy, **changes)], self.fixture.documents, None))

    def test_invalid_model_citation_is_replaced_not_repaired(self):
        self.fixture.chat.return_value = {'message': {'content': render_trusted_answer(
            '错误结论必须丢弃', [dict(self.fixture.by_id['meal_001'], content='假原文X元/天')])}}
        result = self.fixture.service(['meal_001']).answer_question('每天的饭钱补贴，出差期间按多少算？')
        self.assertEqual(result['status'], 'ok')
        self.assertNotIn('错误结论', result['answer'])
        self.assertNotIn('X元', result['answer'])
        self.assertEqual(result['diagnostics']['citation_reason_code'], 'original_mismatch')

    def test_uncertain_leave_and_amounts_remain_guarded(self):
        for question, ids in (
            ('入职只有八个月，能算五天年假吗？', ['leave_001']),
            ('满三年的年假能加上满一年的年假吗？', ['leave_001', 'leave_002']),
            ('二线城市住宿差额能领取吗？', ['hotel_002']),
            ('出差餐饮超过100元能报吗？', ['meal_001']),
            ('商务座没有拿到批准能报销吗？', ['transport_001']),
            ('商务座批准后全额报销吗？', ['transport_001'])):
            result = self.fixture.service(ids).answer_question(question)
            self.assertEqual(result['status'], 'guarded', question)
        self.fixture.chat.assert_not_called()

    def test_transport_payment_does_not_trigger_meal_payment(self):
        question = '出差饭补和普通列车硬座报销的规定是什么？'
        self.assertFalse(meal_risks(question)[0])
        result = self.fixture.service(['meal_001', 'transport_001']).answer_question(question)
        self.assertEqual({p['policy_id'] for p in result['policies']}, {'meal_001', 'transport_001'})
        self.assertNotIn('实际餐饮支出的报销资格', result['answer'])
        self.assertTrue(meal_risks('餐饮超额报销和高铁规定是什么？')[0])

    def test_exclusions_and_incomplete_multi_city_stay_safe(self):
        for question, pid in (('不问住宿，只问餐饮补贴。', 'meal_001'),
                              ('只查交通规定，不查餐饮补贴。', 'transport_001')):
            result = self.fixture.service(['meal_001', 'transport_001']).answer_question(question)
            self.assertEqual([p['policy_id'] for p in result['policies']], [pid])
        result = self.fixture.service(['hotel_002']).answer_question('一线和二线城市住宿标准？')
        self.assertEqual(result['status'], 'guarded')
        self.assertNotIn('500', result['answer'])


if __name__ == '__main__':
    unittest.main()
