"""离线测试：只使用虚构内存数据，不加载模型或访问数据库。"""

import unittest

from pydantic import ValidationError

from .fictional_inventory import INVENTORY
from .schemas import InventoryQuery
from .tools import query_inventory


class InventoryTests(unittest.TestCase):
    def test_all_fictional_records(self):
        self.assertEqual(len(INVENTORY), 6)
        for (product, color, size), quantity in INVENTORY.items():
            with self.subTest(product=product, color=color, size=size):
                result = query_inventory(product, color, size)
                self.assertEqual(result["quantity"], quantity)
                self.assertEqual(result["status"], "in_stock" if quantity > 0 else "out_of_stock")
                self.assertEqual(result["data_source"], "fictional_demo")

    def test_in_stock(self):
        result = query_inventory("基础T恤", "白色", "M")
        self.assertEqual(result["status"], "in_stock")
        self.assertEqual(result["quantity"], 8)

    def test_zero_is_out_of_stock(self):
        result = query_inventory("基础T恤", "白色", "L")
        self.assertEqual(result["status"], "out_of_stock")
        self.assertEqual(result["quantity"], 0)

    def test_product_not_found(self):
        result = query_inventory("羽绒服", "黑色", "M")
        self.assertEqual(result["status"], "product_not_found")
        self.assertIsNone(result["quantity"])

    def test_variant_not_found(self):
        for color, size in (("红色", "M"), ("白色", "XL")):
            result = query_inventory("基础T恤", color, size)
            self.assertEqual(result["status"], "variant_not_found")
            self.assertIsNone(result["quantity"])

    def test_empty_and_whitespace(self):
        for field in ("product", "color", "size"):
            for value in ("", " ", "\t\n"):
                args = dict(product="基础T恤", color="白色", size="M")
                args[field] = value
                with self.subTest(field=field, value=repr(value)), self.assertRaises(ValidationError):
                    query_inventory(**args)

    def test_non_string_arguments(self):
        for field in ("product", "color", "size"):
            for value in (None, 1, True, 1.5, [], {}, b"M"):
                args = dict(product="基础T恤", color="白色", size="M")
                args[field] = value
                with self.subTest(field=field, value=repr(value)), self.assertRaises(ValidationError):
                    query_inventory(**args)

    def test_missing_arguments(self):
        for field in ("product", "color", "size"):
            args = dict(product="基础T恤", color="白色", size="M")
            del args[field]
            with self.assertRaises(ValidationError):
                query_inventory(**args)

    def test_invalid_sizes(self):
        for size in ("m", "XXL", "均码", "M\nL", "42"):
            with self.assertRaises(ValidationError):
                query_inventory("基础T恤", "白色", size)

    def test_extra_fields(self):
        with self.assertRaises(ValidationError):
            query_inventory("基础T恤", "白色", "M", quantity=1)
        with self.assertRaises(ValidationError):
            InventoryQuery.model_validate(dict(product="基础T恤", color="白色", size="M", quantity=1))

    def test_strip_and_valid_size_set(self):
        result = query_inventory(" 基础T恤 ", " 白色 ", " M ")
        self.assertEqual((result["product"], result["color"], result["size"]), ("基础T恤", "白色", "M"))
        self.assertEqual(result["quantity"], 8)
        for size in ("S", "M", "L", "XL"):
            self.assertEqual(InventoryQuery(product="基础T恤", color="白色", size=size).size, size)

    def test_inventory_unchanged_after_queries_and_errors(self):
        before = dict(INVENTORY)
        for args in (("基础T恤", "白色", "M"), ("基础T恤", "白色", "L"),
                     ("未知商品", "黑色", "M"), ("基础T恤", "红色", "M")):
            query_inventory(*args)
        with self.assertRaises(ValidationError):
            query_inventory("", "白色", "M")
        self.assertEqual(dict(INVENTORY), before)
        with self.assertRaises(TypeError):
            INVENTORY[("基础T恤", "白色", "M")] = 99

    def test_returned_result_is_independent(self):
        result = query_inventory("基础T恤", "白色", "M")
        result["quantity"] = 999
        self.assertEqual(query_inventory("基础T恤", "白色", "M")["quantity"], 8)

    def test_model_and_function_use_same_constraints(self):
        args = InventoryQuery.model_validate(dict(product=" 基础T恤 ", color=" 白色 ", size=" M "))
        self.assertEqual(query_inventory(**args.model_dump())["quantity"], 8)
        for field in ("product", "color", "size"):
            data = args.model_dump()
            data[field] = None
            with self.assertRaises(ValidationError):
                InventoryQuery.model_validate(data)


if __name__ == "__main__":
    unittest.main()
