"""标准库完成度测试；只检查历史事实与有依据候选，不调用工具。"""

import json
import unittest

from .progress import completed_domains, progress_state, remaining_tools, requested_domains


class ProgressTests(unittest.TestCase):
    def test_explicit_three_domains(self):
        self.assertEqual(requested_domains("最近营业额为什么下降？帮我调查销售、库存和补货环节。"),
                         ["sales", "inventory", "replenishment"])

    def test_single_domains(self):
        self.assertEqual(requested_domains("请先调用销售查询工具，查询两个期间的营业额、订单数和成交率。"), ["sales"])
        self.assertEqual(requested_domains("请查询基础T恤的补货记录。"), ["replenishment"])
        self.assertEqual(requested_domains("请调查库存。"), ["inventory"])

    def test_ambiguous_or_negative_not_explicit(self):
        for text in ("最近营业额为什么下降？", "请全面调查", "不要调查库存", "不是销售，是库存", "调查销售或库存"):
            self.assertEqual(requested_domains(text), [])

    def test_zero_stock_counts_as_verified_inventory(self):
        history = [{"tool": "query_inventory", "status": "verified", "result": {
            "status": "out_of_stock", "quantity": 0, "record": {"evidence_id": "INV-002"}}}]
        self.assertEqual(completed_domains(history), ["inventory"])

    def test_no_result_or_failed_does_not_complete(self):
        history = [{"tool": "query_inventory", "status": "verified", "result": {"status": "variant_not_found", "quantity": None, "record": None}},
                   {"tool": "query_sales", "status": "failed", "result": {"status": "ok", "periods": [1, 2], "changes": {}}},
                   {"tool": "query_replenishment", "status": "verified", "result": {"status": "no_records", "records": []}}]
        self.assertEqual(completed_domains(history), [])

    def test_progress_uses_actual_successful_history(self):
        history = [{"tool": "query_sales", "status": "verified", "result": {"status": "ok", "periods": [1, 2], "changes": {}}},
                   {"tool": "query_replenishment", "status": "verified", "result": {"status": "ok", "records": [{"evidence_id": "REP-001"}]}}]
        state = progress_state(["sales", "inventory", "replenishment"], history)
        self.assertEqual(state["completed_domains"], ["sales", "replenishment"])
        self.assertEqual(state["missing_domains"], ["inventory"])

    def test_unopened_or_unrequested_candidates_not_eligible(self):
        available = {"query_sales": {"opened": True, "candidates": [{"arguments": {}}]},
                     "query_inventory": {"opened": False, "candidates": []}}
        self.assertEqual(remaining_tools(available, ["inventory"], set()), [])
        self.assertEqual(remaining_tools(available, [], set()), [])

    def test_seen_candidates_cannot_trigger_continuation(self):
        args = {"product": "基础T恤", "color": "白色", "size": "M", "store_id": "SZ-DEMO-001"}
        available = {"query_inventory": {"opened": True, "candidates": [{"arguments": args}]}}
        seen = {("query_inventory", json.dumps(args, sort_keys=True, ensure_ascii=False))}
        self.assertEqual(remaining_tools(available, ["inventory"], seen), [])
        self.assertEqual(remaining_tools(available, ["inventory"], set()), ["query_inventory"])


if __name__ == "__main__":
    unittest.main()
