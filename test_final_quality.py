"""Final bounded quality fixes using simulated resources only."""
import unittest
import test_rag_service as fixtures


class FinalQualityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RagServiceTests()
        self.fixture.setUp()
        self.fixture.chat.side_effect = None
        self.fixture.chat.return_value = {'message': {'content': 'bad model body'}}

    def test_hotel_and_city_meal_paraphrases(self):
        for question, ids, expected in (
            ('去二线城市住宾馆，住宿日标准请告诉我。', ['hotel_002'], 'hotel_002'),
            ('二线城市出差的餐饮补贴每天多少？', ['meal_001', 'hotel_002'], 'meal_001')):
            result = self.fixture.service(ids).answer_question(question)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual([p['policy_id'] for p in result['policies']], [expected])
            self.assertNotIn('bad model body', result['answer'])
            self.assertTrue(result['diagnostics']['model_answer_discarded'])

    def test_known_and_unknown_welfare_are_separated(self):
        result = self.fixture.service(['meal_001', 'hotel_001']).answer_question(
            '出差餐饮补贴和公司健身补贴分别怎么规定？')
        self.assertEqual(result['status'], 'guarded')
        self.assertEqual([p['policy_id'] for p in result['policies']], ['meal_001'])
        self.assertIn('没有已确认', result['answer'])
        self.assertIn('不代表公司不存在', result['answer'])
        self.fixture.chat.assert_not_called()

    def test_separate_meal_reranking_recovers_joint_evidence(self):
        for question, ids in (
            ('出差饭补和普通列车硬座报销规定？', ['meal_001', 'transport_001']),
            ('其他城市住宿实际报多少，饭补每天多少？', ['hotel_003', 'meal_001'])):
            service = self.fixture.service(ids)
            service.reranker.predict.side_effect = lambda pairs: [
                0.95 if pair[0] == '出差餐饮补贴标准是多少？' else
                (0.23 if pair[1] == self.fixture.by_id['meal_001']['content'] else 0.9)
                for pair in pairs]
            result = service.answer_question(question)
            self.assertEqual({p['policy_id'] for p in result['policies']}, set(ids))
            self.assertEqual(result['status'], 'guarded')
            self.assertEqual(len(result['subquery_retrievals']), 1)
            if '实际' in question:
                self.assertIn('无法确认实际可报金额', result['answer'])

    def test_independent_query_still_requires_threshold(self):
        service = self.fixture.service(['meal_001', 'transport_001'], [0.23, 0.9])
        result = service.answer_question('饭补和普通列车硬座报销规定？')
        self.assertEqual([p['policy_id'] for p in result['policies']], ['transport_001'])
        self.assertEqual(result['subquery_retrievals'][0]['accepted'], [])
        self.assertNotIn('100元', result['answer'])

    def test_hotel_balance_recovers_standard_without_payment_promise(self):
        service = self.fixture.service(['hotel_002'])
        service.reranker.predict.side_effect = lambda pairs: [
            0.9 if pair[0] == '二线城市住宿标准是多少？' else 0.4462 for pair in pairs]
        result = service.answer_question('二线城市住宿花了280元，剩余120元会发给我吗？')
        self.assertEqual(result['status'], 'guarded')
        self.assertIn('400元/天', result['answer'])
        self.assertIn('不能确认差额会发放或不发放', result['answer'])
        self.fixture.chat.assert_not_called()


if __name__ == '__main__':
    unittest.main()
