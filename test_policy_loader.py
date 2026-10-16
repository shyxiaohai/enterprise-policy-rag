"""仅使用标准库及临时政策文件，不导入 main.py。"""

from pathlib import Path
import tempfile
import unittest

from policy_loader import load_policy_documents


POLICY_PATH = Path(__file__).resolve().parent / "knowledge_base" / "company_policy.txt"


class PolicyLoaderTests(unittest.TestCase):
    def setUp(self):
        self.original = POLICY_PATH.read_text(encoding="utf-8-sig")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "company_policy.txt"

    def load_text(self, text):
        self.path.write_text(text, encoding="utf-8-sig")
        return load_policy_documents(self.path)

    def test_real_file_structure_and_unique_ids(self):
        policies = load_policy_documents()
        self.assertEqual(len(policies), 7)
        self.assertEqual(len({p["policy_id"] for p in policies}), 7)
        self.assertEqual({p["policy_id"] for p in policies}, {
            "hotel_001", "hotel_002", "hotel_003", "transport_001",
            "meal_001", "leave_001", "leave_002"
        })
        for policy in policies:
            self.assertEqual(policy["source"], "company_policy.txt")
            expected = {"content", "source", "policy_id"}
            if policy["policy_id"].startswith("hotel_"):
                expected |= {"category", "city_level", "amount", "unit", "scope"}
                self.assertIs(type(policy["amount"]), int)
                self.assertEqual(policy["category"], "hotel")
                self.assertEqual(policy["unit"], "day")
                self.assertEqual(policy["scope"], "city")
            self.assertEqual(set(policy), expected)

    def test_real_amounts_and_transport_restriction(self):
        policies = {p["policy_id"]: p for p in load_policy_documents()}
        for policy_id, amount, city in [
            ("hotel_001", 500, "tier_1"), ("hotel_002", 400, "tier_2"),
            ("hotel_003", 300, "other")
        ]:
            self.assertEqual(policies[policy_id]["amount"], amount)
            self.assertEqual(policies[policy_id]["city_level"], city)
        self.assertIn("100元", policies["meal_001"]["content"])
        self.assertIn("未经批准不得报销商务座。", policies["transport_001"]["content"])

    def test_amounts_are_read_from_file(self):
        changed = self.original.replace("500元", "520元").replace("100元", "120元")
        policies = {p["policy_id"]: p for p in self.load_text(changed)}
        self.assertEqual(policies["hotel_001"]["amount"], 520)
        self.assertIn("520元/天", policies["hotel_001"]["content"])
        self.assertIn("120元", policies["meal_001"]["content"])

    def test_section_and_policy_order_does_not_change_identity(self):
        lines = self.original.splitlines()
        title = lines[0]
        blocks = []
        for line in lines[1:]:
            if not line.strip():
                continue
            if line[0].isdigit() and "." in line:
                blocks.append([line])
            else:
                blocks[-1].append(line)
        blocks[0][1:] = reversed(blocks[0][1:])
        blocks[3][1:] = reversed(blocks[3][1:])
        reordered = title + "\n" + "\n".join("\n".join(b) for b in reversed(blocks))
        self.assertEqual(self.load_text(reordered), load_policy_documents())

    def test_missing_file(self):
        with self.assertRaisesRegex(FileNotFoundError, "政策文件不存在"):
            load_policy_documents(Path(self.temp.name) / "missing.txt")

    def test_invalid_formats(self):
        cases = {
            "空文件": " \n",
            "标题错误": self.original.replace("公司员工报销制度", "其他制度"),
            "章节缺失": self.original.split("4. 年假制度")[0],
            "未知章节": self.original + "\n5. 新政策\n未知内容\n",
            "重复章节": self.original + "\n3. 餐饮补贴\n重复\n",
            "重复政策": self.original.replace("一线城市：500元/天", "一线城市：500元/天\n一线城市：500元/天"),
            "缺少城市": self.original.replace("二线城市：400元/天", ""),
            "错误金额": self.original.replace("500元/天", "五百元/天"),
            "零金额": self.original.replace("500元/天", "0元/天"),
            "错误单位": self.original.replace("500元/天", "500元/月"),
            "交通限制缺失": self.original.replace("未经批准不得报销商务座。", ""),
            "餐饮重复": self.original.replace("出差期间每天餐饮补贴标准为100元。", "出差期间每天餐饮补贴标准为100元。\n出差期间每天餐饮补贴标准为100元。"),
            "未知年假档位": self.original.replace("工作满3年", "工作满4年"),
            "年假重复": self.original.replace("工作满3年", "工作满1年"),
            "额外正文": self.original + "\n住宿实行固定补贴。"
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.load_text(text)

    def test_invalid_encoding(self):
        self.path.write_bytes(b"\xff\xfe\x00")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            load_policy_documents(self.path)


if __name__ == "__main__":
    unittest.main()
