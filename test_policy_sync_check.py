"""模拟 collection，仅允许一次带来源限制的 get。"""
import copy
from pathlib import Path
import tempfile
import unittest

from policy_loader import load_policy_documents
from policy_sync_check import check_policy_sync


class FakeCollection:
    name = "company_policy"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return copy.deepcopy(self.result)

    def __getattr__(self, name):
        if name in {"add", "update", "upsert", "delete", "reset", "modify"}:
            raise AssertionError("禁止调用写接口：" + name)
        raise AttributeError(name)


class SyncCheckTests(unittest.TestCase):
    def setUp(self):
        self.policies = load_policy_documents()
        self.result = {"ids": [], "documents": [], "metadatas": []}
        for p in self.policies:
            self.result["ids"].append(p["policy_id"])
            self.result["documents"].append(p["content"])
            metadata = {"source": p["source"], "policy_id": p["policy_id"]}
            for key, default in {"category": "", "city_level": "", "amount": 0, "unit": "", "scope": ""}.items():
                metadata[key] = p.get(key, default)
            self.result["metadatas"].append(metadata)

    def test_existing_and_read_only(self):
        collection = FakeCollection(self.result)
        before = copy.deepcopy(self.result)
        report = check_policy_sync(collection)
        self.assertEqual(len(report["existing"]), 7)
        self.assertEqual(report["needs_review"], [])
        self.assertEqual(collection.calls, [{"where": {"source": "company_policy.txt"}, "include": ["documents", "metadatas"]}])
        self.assertEqual(self.result, before)

    def test_new_changed_and_old(self):
        for values in self.result.values():
            values.pop()
        self.result["documents"][0] = "旧正文"
        self.result["ids"].append("old_001")
        self.result["documents"].append("旧政策")
        self.result["metadatas"].append({"source": "company_policy.txt", "policy_id": "old_001"})
        report = check_policy_sync(FakeCollection(self.result))
        self.assertEqual(report["new"], ["leave_002"])
        self.assertEqual(report["missing_in_database"], ["leave_002"])
        self.assertEqual(report["changed"][0]["policy_id"], "hotel_001")
        self.assertEqual(report["old_ids"][0]["policy_id"], "old_001")

    def test_empty_database(self):
        report = check_policy_sync(FakeCollection({key: [] for key in self.result}))
        self.assertEqual(len(report["new"]), 7)

    def test_metadata_and_record_id_mismatch(self):
        self.result["ids"][0] = "different_id"
        self.result["metadatas"][0]["city_level"] = "other"
        report = check_policy_sync(FakeCollection(self.result))
        self.assertEqual(len(report["needs_review"]), 2)
        self.assertNotIn("hotel_001", report["existing"])

    def test_duplicate_policy_and_missing_metadata(self):
        self.result["metadatas"][1]["policy_id"] = "hotel_001"
        del self.result["metadatas"][2]["policy_id"]
        report = check_policy_sync(FakeCollection(self.result))
        self.assertEqual(len(report["needs_review"]), 3)
        self.assertNotIn("hotel_001", report["existing"])
        self.assertEqual(report["new"], [])

    def test_wrong_source_collection_and_malformed_results(self):
        self.result["metadatas"][0]["source"] = "other.txt"
        with self.assertRaises(ValueError):
            check_policy_sync(FakeCollection(self.result))
        collection = FakeCollection(self.result)
        collection.name = "other"
        with self.assertRaises(ValueError):
            check_policy_sync(collection)
        self.assertEqual(collection.calls, [])
        with self.assertRaises(ValueError):
            check_policy_sync(FakeCollection({"ids": [], "documents": ["extra"], "metadatas": []}))

    def test_parse_failure_prevents_database_read(self):
        collection = FakeCollection(self.result)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "company_policy.txt"
            path.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                check_policy_sync(collection, path)
        self.assertEqual(collection.calls, [])

    def test_missing_body_is_not_new(self):
        for body in (None, "", "  ", 123):
            with self.subTest(body=body):
                self.result["documents"][0] = body
                report = check_policy_sync(FakeCollection(self.result))
                self.assertEqual(report["new"], [])
                self.assertEqual(report["missing_in_database"], [])
                self.assertNotIn("hotel_001", report["existing"])
                self.assertEqual(report["changed"], [])
                self.assertTrue(report["needs_review"])

    def test_metadata_types_are_strict(self):
        for index, value in ((0, 500.0), (0, "500"), (3, False), (3, 0.0)):
            with self.subTest(index=index, value=value):
                result = copy.deepcopy(self.result)
                result["metadatas"][index]["amount"] = value
                report = check_policy_sync(FakeCollection(result))
                self.assertNotIn(result["ids"][index], report["existing"])
                fields = report["needs_review"][0]["fields"]
                self.assertIn("amount", fields)
                self.assertNotEqual(fields["amount"]["database_type"], "int")

    def test_changed_and_review_can_overlap(self):
        self.result["documents"][0] = "旧正文"
        self.result["metadatas"][0]["city_level"] = "other"
        report = check_policy_sync(FakeCollection(self.result))
        self.assertEqual(report["changed"][0]["policy_id"], "hotel_001")
        self.assertEqual(report["needs_review"][0]["policy_id"], "hotel_001")
        self.assertNotIn("hotel_001", report["existing"])

    def test_old_identity_mismatch(self):
        self.result["ids"].append("legacy_record")
        self.result["documents"].append("旧正文")
        self.result["metadatas"].append({"source": "company_policy.txt", "policy_id": "old_policy"})
        report = check_policy_sync(FakeCollection(self.result))
        self.assertEqual(report["old_ids"][0]["policy_id"], "old_policy")
        self.assertEqual(report["needs_review"][0]["policy_id"], "old_policy")

    def test_missing_source_aborts_report(self):
        for metadata in (None, {}, {"policy_id": "hotel_001"}):
            with self.subTest(metadata=metadata):
                self.result["metadatas"][0] = metadata
                with self.assertRaisesRegex(ValueError, "缺失 source"):
                    check_policy_sync(FakeCollection(self.result))

    def test_multiple_sources_filtered(self):
        class FilteringCollection(FakeCollection):
            def get(self, **kwargs):
                self.calls.append(kwargs)
                filtered = {key: [] for key in ("ids", "documents", "metadatas")}
                for index, metadata in enumerate(self.result["metadatas"]):
                    if metadata.get("source") == kwargs["where"]["source"]:
                        for key in filtered:
                            filtered[key].append(copy.deepcopy(self.result[key][index]))
                return filtered

        self.result["ids"].append("external")
        self.result["documents"].append("外部政策")
        self.result["metadatas"].append({"source": "other.txt", "policy_id": "hotel_001"})
        report = check_policy_sync(FilteringCollection(self.result))
        self.assertEqual(len(report["existing"]), 7)
        self.assertEqual(report["needs_review"], [])
        self.assertEqual(report["old_ids"], [])

    def test_get_failure_aborts_report(self):
        class FailingCollection(FakeCollection):
            def get(self, **kwargs):
                raise OSError("模拟读取失败")

        with self.assertRaisesRegex(RuntimeError, "未生成报告") as caught:
            check_policy_sync(FailingCollection(self.result))
        self.assertIsInstance(caught.exception.__cause__, OSError)

    def test_additional_parse_failures_precede_get(self):
        original = (Path(__file__).resolve().parent / "knowledge_base" / "company_policy.txt").read_text(encoding="utf-8-sig")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "company_policy.txt"
            collection = FakeCollection(self.result)
            with self.assertRaises(FileNotFoundError):
                check_policy_sync(collection, path)
            for text in (original + "\n5. 未知章节\n正文", original.replace("一线城市：500元/天", "一线城市：500元/天\n一线城市：500元/天")):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    check_policy_sync(collection, path)
            self.assertEqual(collection.calls, [])


if __name__ == "__main__":
    unittest.main()
