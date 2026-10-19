"""候选收集的标准库测试；许可回调用stub，实际来源授权由Fake Agent测试覆盖。"""

import unittest

from .availability import tool_availability


def permit(name, args, user, history):
    return {key: {"source": "test_stub", "value": value} for key, value in args.items()}


def opened(decisions):
    return {name for name, row in decisions.items() if row["opened"]}


class AvailabilityTests(unittest.TestCase):
    def test_no_conditions_only_sales(self):
        self.assertEqual(opened(tool_availability({}, [], permit)), {"query_sales"})

    def test_user_product(self):
        decisions = tool_availability({"product": "基础T恤"}, [], permit)
        self.assertEqual(opened(decisions), {"query_sales", "query_replenishment"})

    def test_user_complete_variant(self):
        decisions = tool_availability({"product": "基础T恤", "color": "白色", "size": "M"}, [], permit)
        self.assertEqual(opened(decisions), {"query_sales", "query_replenishment", "query_inventory"})

    def test_sales_does_not_invent_variant(self):
        history = [{"tool": "query_sales", "status": "verified", "result": {"status": "ok", "hot_products": [{"product": "基础T恤"}]}}]
        decisions = tool_availability({}, history, permit)
        self.assertTrue(decisions["query_replenishment"]["opened"])
        self.assertFalse(decisions["query_inventory"]["opened"])

    def test_replenishment_keeps_variants_separate(self):
        history = [{"tool": "query_replenishment", "status": "verified", "result": {"status": "ok", "records": [
            {"product": "基础T恤", "items": [{"color": "白色", "size": "M"}, {"color": "黑色", "size": "L"}]}]}}]
        rows = tool_availability({}, history, permit)["query_inventory"]["candidates"]
        self.assertEqual([(r["arguments"]["color"], r["arguments"]["size"]) for r in rows], [("白色", "M"), ("黑色", "L")])

    def test_failed_and_missing_results_not_used(self):
        history = [{"tool": "query_sales", "status": "failed", "result": {"status": "ok", "hot_products": [{"product": "基础T恤"}]}},
                   {"tool": "query_replenishment", "status": "verified", "result": None}]
        self.assertEqual(opened(tool_availability({}, history, permit)), {"query_sales"})

    def test_authoritative_source_callback_can_deny_candidates(self):
        def deny(name, args, user, history):
            return permit(name, args, user, history) if name == "query_sales" else None

        decisions = tool_availability({"product": "基础T恤", "color": "白色", "size": "M"}, [], deny)
        self.assertEqual(opened(decisions), {"query_sales"})

    def test_duplicate_candidates_removed(self):
        history = [{"tool": "query_sales", "status": "verified", "result": {"status": "ok", "hot_products": [{"product": "基础T恤"}]}}]
        rows = tool_availability({"product": "基础T恤"}, history, permit)["query_replenishment"]["candidates"]
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
