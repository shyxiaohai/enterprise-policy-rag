"""只使用现有模拟资源，不连接数据库或模型。"""
from test_rag_service import RagServiceTests


class EvidenceGuardTests(RagServiceTests):
    # 继承原服务回归测试，同时检查新保护不会破坏既有行为。
    def test_partial_hotel_meal_evidence(self):
        for question in ("二线城市住宿和餐饮补贴分别多少？", "二线城市酒店标准及吃饭补助是多少？"):
            result = self.service(["hotel_002", "meal_001"], [0.4834, 0.8013]).answer_question(question)
            self.assertEqual(result["status"], "guarded")
            self.assertIn("住宿：本次筛选后的适用证据不足", result["answer"])
            self.assertIn(self.by_id["meal_001"]["content"], result["answer"])
            self.assertNotIn("500", result["answer"])
            self.assertEqual([p["policy_id"] for p in result["policies"]], ["meal_001"])
        self.chat.assert_not_called()

    def test_all_filtered_is_not_database_absence(self):
        result = self.service(["hotel_003", "transport_001"], [0.4262, 0.4960]).answer_question("其他城市住宿标准和火车座位规定？")
        self.assertEqual(result["status"], "no_policy")
        self.assertIn("筛选后无可用", result["answer"])
        self.assertIn("不代表", result["answer"])
        self.chat.assert_not_called()

    def test_uncovered_transport(self):
        for question in ("飞机头等舱可以报销吗？", "航空经济舱是否允许报销？", "出租车费用能报销吗？"):
            result = self.service(["transport_001"]).answer_question(question)
            self.assertEqual(result["status"], "guarded")
            self.assertIn("无法确认可以或不可以", result["answer"])
        self.chat.assert_not_called()

    def test_under_one_year(self):
        for question in ("工作未满1年有几天年假？", "入职半年有年假吗？", "工作11个月年假多少？"):
            result = self.service(["leave_001", "leave_002"]).answer_question(question)
            self.assertEqual(result["status"], "guarded")
            self.assertIn("不能据此认定为0天", result["answer"])
        self.chat.assert_not_called()

    def test_normal_transport_and_leave_still_generate(self):
        for ids, question in ((["transport_001"], "未经批准可以报销商务座吗？"), (["leave_001"], "满1年有几天年假？"), (["leave_002"], "工作满3年年假规定？")):
            self.assertEqual(self.service(ids).answer_question(question)["status"], "ok")
        self.assertEqual(self.chat.call_count, 3)

    def test_partial_hotel_transport_and_complete_joint(self):
        result = self.service(["hotel_003", "transport_001"], [0.4, 0.8]).answer_question("其他城市住宿标准和交通规定？")
        self.assertEqual(result["status"], "guarded")
        self.assertIn(self.by_id["transport_001"]["content"], result["answer"])
        complete = self.service(["hotel_003", "transport_001"]).answer_question("其他城市住宿标准和交通规定？")
        self.assertEqual(complete["status"], "guarded")
        self.assertIn(self.by_id["hotel_003"]["content"], complete["answer"])
        self.assertIn(self.by_id["transport_001"]["content"], complete["answer"])
        self.chat.assert_not_called()

    def test_transport_conditions_preserved_without_model_rewrite(self):
        wrong = "商务座出差期间不得报销。政策编号：transport_001。未经批准"
        self.chat.return_value = {"message": {"content": wrong}}
        for question in ("出差交通报销规定是什么？", "高铁可以报销哪些座位？", "介绍一下火车报销政策"):
            result = self.service(["transport_001"]).answer_question(question)
            self.assertEqual(result["status"], "guarded")
            self.assertIn(self.by_id["transport_001"]["content"], result["answer"])
            self.assertNotIn("商务座出差期间不得报销。", result["answer"])
        # 即使错误文本别处含“未经批准”也不被当作语义正确，而是完全跳过生成。
        self.chat.assert_not_called()

    def test_meal_transport_explanation_both_orders(self):
        for question in ("出差餐饮补贴及高铁报销规定是什么？", "火车可报销哪些座位，吃饭每天补助标准是什么？"):
            result = self.service(["meal_001", "transport_001"]).answer_question(question)
            self.assertEqual(result["status"], "guarded")
            for policy_id in ("meal_001", "transport_001"):
                self.assertIn(self.by_id[policy_id]["content"], result["answer"])
            self.assertEqual({p["policy_id"] for p in result["policies"]}, {"meal_001", "transport_001"})
        self.chat.assert_not_called()

    def test_unrecognized_subquestion_not_claimed_answered(self):
        result = self.service(["transport_001"]).answer_question("交通规定是什么，审批需要多久？")
        self.assertEqual(result["status"], "guarded")
        self.assertIn("其他条件、计算或子问题请拆分提问", result["answer"])
        self.assertNotIn("审批需要1天", result["answer"])
        self.chat.assert_not_called()

    def test_unknown_expression_outside_scope(self):
        # 不宣称理解所有隐喻或口语；识别不了的表达保持原路径。
        result = self.service(["transport_001"]).answer_question("这个怎么走账？")
        self.assertEqual(result["status"], "guarded")
        self.assertIn("人工复核", result["answer"])
        self.chat.assert_called_once()

    def test_business_seat_approval_does_not_imply_payment(self):
        questions = (
            "批准后商务座一定可以全额报销吗？",
            "商务座全额报销可以吗，领导已经同意？",
            "商务座花800元，审批通过能报多少？",
            "已获批的商务座能报80%吗？",
            "领导批了，商务座就可以报销吗？",
            "商务座报销比例是多少？",
            "商务座花1200元能报1200元吗？",
            "商务座审核通过后可以全部报销吗？"
        )
        for question in questions:
            with self.subTest(question=question):
                result = self.service(["transport_001"]).answer_question(question)
                self.assertEqual(result["status"], "guarded")
                self.assertIn("无法确认批准后一定可报", result["answer"])
                self.assertIn("未经批准不得报销", result["answer"])
                self.assertEqual(result["policies"][0]["policy_id"], "transport_001")
        self.chat.assert_not_called()

    def test_valid_id_does_not_validate_false_conclusion(self):
        wrong = "批准后商务座一定可以全额报销。政策编号：transport_001"
        self.chat.return_value = {"message": {"content": wrong}}
        result = self.service(["transport_001"]).answer_question("商务座获批后可以全额报销吗？")
        self.assertEqual(result["status"], "guarded")
        self.assertNotEqual(result["answer"], wrong)
        self.assertNotIn("批准后商务座一定可以全额报销。", result["answer"])
        self.chat.assert_not_called()

    def test_approval_missing_evidence_and_meal_joint(self):
        result = self.service(["meal_001"]).answer_question("商务座批准后能全额报销吗？餐饮补贴标准是多少？")
        self.assertEqual(result["status"], "guarded")
        self.assertIn("没有适用交通依据", result["answer"])
        self.assertIn(self.by_id["meal_001"]["content"], result["answer"])
        self.assertNotIn("transport_001", result["answer"])
        self.chat.assert_not_called()
