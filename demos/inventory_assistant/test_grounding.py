"""仅标准库的原始问题核对测试，无模型及Pydantic依赖。"""

import unittest

from .grounding import check_argument_support, identify_conditions


class GroundingTests(unittest.TestCase):
    def test_complete(self):
        expected = {"product": "基础T恤", "color": "白色", "size": "M"}
        for text in ("基础T恤白色M码还有几件？", "请问基础T恤白色M码有货吗？",
                     "白色m码的基础T恤有货吗？", "查一下基础T恤 白色 m 码库存"):
            self.assertEqual(identify_conditions(text), expected)

    def test_explicit_fields_and_unknown_product(self):
        self.assertEqual(identify_conditions("商品：羽绒服，颜色：白色，尺码：M"),
                         {"product": "羽绒服", "color": "白色", "size": "M"})

    def test_missing(self):
        for text in ("基础T恤有货吗？", "基础T恤白色有货吗？", "白色M码有货吗？"):
            self.assertIsNone(identify_conditions(text))

    def test_ambiguous_or_negated(self):
        for text in ("基础T恤白色或黑色M码", "不是基础T恤白色M码", "例如基础T恤白色M码",
                     "基础T恤白色M码和L码", '"基础T恤白色M码"', "如果基础T恤白色M码有货", "基础T恤白色M码改黑色"):
            self.assertIsNone(identify_conditions(text))

    def test_unknown_free_expression_rejected(self):
        for text in ("羽绒服白色M码有货吗？", "基础T恤白色中码有货吗？", "基础T恤白色M码，帮我随便决定"):
            self.assertIsNone(identify_conditions(text))

    def test_color_and_size_mismatch(self):
        args = {"product": "基础T恤", "color": "白色", "size": "M"}
        for text in ("基础T恤黑色M码有货吗？", "基础T恤白色L码有货吗？", "休闲衬衫白色M码有货吗？"):
            self.assertEqual(check_argument_support(text, args)["reason"], "argument_mismatch")

    def test_exact_match(self):
        args = {"product": "基础T恤", "color": "白色", "size": "M"}
        self.assertTrue(check_argument_support("基础T恤白色M码有货吗？", args)["supported"])

    def test_missing_support(self):
        result = check_argument_support("基础T恤有货吗？", {"product": "基础T恤", "color": "白色", "size": "M"})
        self.assertFalse(result["supported"])
        self.assertIsNone(result["identified"])


if __name__ == "__main__":
    unittest.main()
