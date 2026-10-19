"""标准库检查原始虚构数据，不依赖Pydantic或模型。"""

from datetime import datetime
from decimal import Decimal
import unittest

from . import fictional_data as data


class DataTests(unittest.TestCase):
    def test_sales_arithmetic(self):
        for row in data.SALES:
            self.assertEqual(row["revenue_fen"], row["orders"] * row["average_order_fen"])
        self.assertEqual(Decimal(data.SALES[0]["orders"])/data.SALES[0]["visitors"], Decimal("0.3"))
        self.assertEqual(data.SALES[1]["orders"] * 30, data.SALES[1]["visitors"] * 7)

    def test_comparable_periods(self):
        durations = [datetime.fromisoformat(p["end_exclusive"])-datetime.fromisoformat(p["start_at"]) for p in data.SALES]
        self.assertEqual(durations[0], durations[1])
        self.assertEqual(durations[0].days, 7)
        self.assertEqual(data.SALES[0]["end_exclusive"], data.SALES[1]["start_at"])

    def test_common_catalog_and_variants(self):
        names = {p["product"] for p in data.CATALOG}
        variants = {(p["product"], p["color"], p["size"]) for p in data.INVENTORY}
        self.assertEqual(len(variants), len(data.INVENTORY))
        for row in data.INVENTORY:
            self.assertIn(row["product"], names)
        for row in data.REPLENISHMENTS:
            self.assertIn(row["product"], names)
            for item in row["items"]:
                self.assertIn((row["product"], item["color"], item["size"]), variants)

    def test_time_ordering(self):
        snapshot = datetime.fromisoformat(data.SNAPSHOT_AT)
        for row in data.REPLENISHMENTS:
            times = [datetime.fromisoformat(row[k]) for k in ("requested_at", "approved_at", "purchased_at", "arrived_at")]
            self.assertEqual(times, sorted(times))
            self.assertLess(snapshot, times[-1])
            self.assertLessEqual(times[-1], datetime.fromisoformat(data.AS_OF))
        for row in data.INVENTORY:
            if row["stockout_since"] is not None:
                self.assertEqual(row["quantity"], 0)
                self.assertLessEqual(datetime.fromisoformat(row["stockout_since"]), snapshot)

    def test_immutable_and_independent_copy(self):
        before = data.copy_data(data.REPLENISHMENTS)
        copy = data.copy_data(data.REPLENISHMENTS)
        copy[0]["items"][0]["received_quantity"] = 999
        self.assertEqual(data.copy_data(data.REPLENISHMENTS), before)
        with self.assertRaises(TypeError):
            data.INVENTORY[0]["quantity"] = 999

    def test_unique_evidence(self):
        ids = [row["evidence_id"] for group in (data.CATALOG, data.SALES, data.INVENTORY, data.REPLENISHMENTS) for row in group]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
