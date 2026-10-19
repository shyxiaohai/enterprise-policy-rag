"""三个工具的离线契约及跨工具一致性测试。"""

from copy import deepcopy
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from . import fictional_data as data
from .schemas import InventoryResponse, ReplenishmentResponse, SalesResponse
from .tools import QueryFailure, query_inventory, query_replenishment, query_sales


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.before = [data.copy_data(group) for group in (data.SALES, data.INVENTORY, data.REPLENISHMENTS, data.CATALOG)]

    def tearDown(self):
        self.assertEqual(self.before, [data.copy_data(group) for group in (data.SALES, data.INVENTORY, data.REPLENISHMENTS, data.CATALOG)])

    def test_sales_values_and_formulas(self):
        result = query_sales()
        self.assertEqual(result["status"], "ok")
        self.assertEqual([p["conversion_rate_pct"] for p in result["periods"]], [30.0, 23.3333])
        self.assertEqual([p["revenue_fen"] for p in result["periods"]], [18900000, 14014000])
        for p in result["periods"]:
            self.assertEqual(p["revenue_fen"], p["orders"] * p["average_order_fen"])
        self.assertIn("rounding", result["basis"])

    def test_sales_changes(self):
        changes = query_sales()["changes"]
        self.assertEqual(changes, dict(visitors_change=0, orders_change=-140,
            average_order_change_fen=-1400, revenue_change_fen=-4886000,
            revenue_change_pct=-25.8519, conversion_change_pp=-6.6667))

    def test_sales_missing_period(self):
        result = query_sales(comparison="P9")
        self.assertEqual(result["status"], "period_not_found")
        self.assertEqual(result["periods"], [])
        self.assertIsNone(result["changes"])

    def test_missing_store_all_tools(self):
        results = [query_sales(store_id="unknown"), query_inventory("基础T恤", "白色", "M", store_id="unknown"),
                   query_replenishment("基础T恤", store_id="unknown")]
        self.assertTrue(all(r["status"] == "store_not_found" for r in results))

    def test_inventory_positive(self):
        result = query_inventory("基础T恤", "白色", "S")
        self.assertEqual(result["status"], "in_stock")
        self.assertEqual(result["quantity"], 5)

    def test_inventory_zero(self):
        result = query_inventory("基础T恤", "白色", "M")
        self.assertEqual(result["status"], "out_of_stock")
        self.assertEqual(result["quantity"], 0)
        self.assertEqual(result["record"]["stockout_since"], "2026-09-09T10:00:00+08:00")

    def test_inventory_product_not_found(self):
        result = query_inventory("羽绒服", "白色", "M")
        self.assertEqual(result["status"], "product_not_found")
        self.assertIsNone(result["quantity"])
        self.assertIsNone(result["record"])

    def test_inventory_variant_not_found(self):
        result = query_inventory("基础T恤", "黑色", "M")
        self.assertEqual(result["status"], "variant_not_found")
        self.assertIsNone(result["quantity"])

    def test_replenishment_nodes_and_intervals(self):
        result = query_replenishment("基础T恤")
        self.assertEqual(result["status"], "ok")
        row = result["records"][0]
        self.assertEqual(row["approval_wait_days"], 3.0)
        self.assertEqual(row["arrival_delay_days"], 4.0)
        self.assertEqual(row["purchase_id"], "PO-001")
        self.assertEqual(sum(p["received_quantity"] for p in row["items"]), 50)

    def test_replenishment_no_records(self):
        result = query_replenishment("直筒长裤")
        self.assertEqual(result["status"], "no_records")
        self.assertEqual(result["records"], [])

    def test_replenishment_product_not_found(self):
        self.assertEqual(query_replenishment("未知商品")["status"], "product_not_found")

    def test_invalid_arguments(self):
        calls = [lambda: query_sales(baseline="P1", comparison="P1"),
                 lambda: query_sales(baseline="P2", comparison="P1"),
                 lambda: query_sales(store_id=1), lambda: query_sales(extra="x"),
                 lambda: query_inventory("", "白色", "M"),
                 lambda: query_inventory("基础T恤", None, "M"),
                 lambda: query_inventory("基础T恤", "白色", "XXL"),
                 lambda: query_inventory("基础T恤", "白色"),
                 lambda: query_inventory("基础T恤", "白色", "M", quantity=1),
                 lambda: query_replenishment(" "), lambda: query_replenishment(True),
                 lambda: query_replenishment("基础T恤", approve=True)]
        for call in calls:
            with self.assertRaises(ValidationError):
                call()

    def test_outputs_validate(self):
        SalesResponse.model_validate(query_sales())
        InventoryResponse.model_validate(query_inventory("基础T恤", "白色", "M"))
        ReplenishmentResponse.model_validate(query_replenishment("基础T恤"))

    def test_returned_data_independent(self):
        result = query_replenishment("基础T恤")
        result["records"][0]["items"][0]["received_quantity"] = 999
        self.assertEqual(query_replenishment("基础T恤")["records"][0]["items"][0]["received_quantity"], 30)

    def test_cross_tool_scope_and_evidence(self):
        sales = query_sales()
        for hot in sales["hot_products"]:
            self.assertEqual(sales["hot_product_period_id"], "P1")
            for stock in data.INVENTORY:
                if stock["product"] != hot["product"]:
                    continue
                inv = query_inventory(stock["product"], stock["color"], stock["size"])
                rep = query_replenishment(stock["product"])
                self.assertEqual(inv["store_id"], sales["store_id"])
                self.assertEqual(inv["as_of"], rep["as_of"])
                self.assertEqual(inv["data_source"], sales["data_source"])
                self.assertIn(stock["evidence_id"], inv["evidence_ids"])
        self.assertEqual(sales["periods"][1]["start_at"][:10], "2026-09-08")

    def test_corrupt_data_is_failure_not_missing(self):
        damaged = data.copy_data(data.SALES)
        damaged[0]["revenue_fen"] = 1
        with patch.object(data, "SALES", damaged):
            with self.assertRaises(QueryFailure):
                query_sales()
        damaged_stock = data.copy_data(data.INVENTORY)
        damaged_stock[1]["quantity"] = -1
        with patch.object(data, "INVENTORY", damaged_stock):
            with self.assertRaises(QueryFailure):
                query_inventory("基础T恤", "白色", "M")

    def test_replenishment_corrupt_timeline(self):
        damaged = data.copy_data(data.REPLENISHMENTS)
        damaged[0]["approved_at"] = "2026-09-01T10:00:00+08:00"
        with patch.object(data, "REPLENISHMENTS", damaged):
            with self.assertRaises(QueryFailure):
                query_replenishment("基础T恤")

    def test_fact_only_outputs(self):
        for result in (query_sales(), query_inventory("基础T恤", "白色", "M"), query_replenishment("基础T恤")):
            self.assertNotIn("conclusion", result)
            self.assertNotIn("cause", result)


if __name__ == "__main__":
    unittest.main()
