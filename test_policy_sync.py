"""只使用模拟数据库验证同步，不执行真实同步入口。"""

import builtins
from copy import deepcopy
import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import policy_sync
from policy_loader import load_policy_documents


class FakeCollection:
    def __init__(self, records=None, fail_id=None):
        self.records = deepcopy(records or {})
        self.calls = []
        self.fail_id = fail_id

    def get(self):
        self.calls.append(("get",))
        return {"ids": list(self.records)}

    def write(self, operation, **kwargs):
        policy_id = kwargs["ids"][0]
        self.calls.append((operation, policy_id))
        if policy_id == self.fail_id:
            raise RuntimeError("模拟写入失败")
        self.records[policy_id] = deepcopy(kwargs)

    def add(self, **kwargs):
        self.write("add", **kwargs)

    def update(self, **kwargs):
        self.write("update", **kwargs)

    def __getattr__(self, name):
        if name in ("delete", "reset", "upsert"):
            raise AssertionError("禁止调用：" + name)
        raise AttributeError(name)


class PolicySyncTests(unittest.TestCase):
    def setUp(self):
        self.documents = load_policy_documents()

    def test_new_records_preserve_content_and_metadata(self):
        collection = FakeCollection()
        before = deepcopy(self.documents)
        self.assertEqual(policy_sync.sync_policies(collection, self.documents), {"processed": 7, "added": 7, "updated": 0})
        self.assertEqual(self.documents, before)
        for document in self.documents:
            record = collection.records[document["policy_id"]]
            self.assertEqual(record["documents"], [document["content"]])
            metadata = record["metadatas"][0]
            for key, default in {"category": "", "city_level": "", "amount": 0, "unit": "", "scope": ""}.items():
                self.assertEqual(metadata[key], document.get(key, default))
                self.assertIs(type(metadata[key]), type(document.get(key, default)))
            self.assertEqual(metadata["source"], document["source"])
            self.assertEqual(metadata["policy_id"], document["policy_id"])

    def test_existing_update_and_old_record_preserved(self):
        collection = FakeCollection({"hotel_001": "旧正文", "old_policy": "保留"})
        result = policy_sync.sync_policies(collection, self.documents)
        self.assertEqual(result, {"processed": 7, "added": 6, "updated": 1})
        self.assertIn(("update", "hotel_001"), collection.calls)
        self.assertEqual(collection.records["old_policy"], "保留")

    def test_repeated_sync_no_duplicates(self):
        collection = FakeCollection()
        policy_sync.sync_policies(collection, self.documents)
        result = policy_sync.sync_policies(collection, self.documents)
        self.assertEqual(result, {"processed": 7, "added": 0, "updated": 7})
        self.assertEqual(len(collection.records), 7)

    def test_partial_failure_stops(self):
        collection = FakeCollection(fail_id="hotel_002")
        with self.assertRaises(policy_sync.PolicySyncError) as caught:
            policy_sync.sync_policies(collection, self.documents)
        self.assertEqual(caught.exception.processed, 1)
        self.assertEqual(caught.exception.policy_id, "hotel_002")
        self.assertEqual(collection.calls, [("get",), ("add", "hotel_001"), ("add", "hotel_002")])

    def test_get_failure_reports_zero(self):
        collection = FakeCollection()
        collection.get = Mock(side_effect=RuntimeError("读取失败"))
        with self.assertRaises(policy_sync.PolicySyncError) as caught:
            policy_sync.sync_policies(collection, self.documents)
        self.assertEqual(caught.exception.processed, 0)
        self.assertEqual(collection.records, {})

    def test_parse_failure_before_database_import(self):
        original_import = builtins.__import__

        def forbid_database(name, *args, **kwargs):
            if name == "chromadb":
                raise AssertionError("解析失败后不能导入数据库")
            return original_import(name, *args, **kwargs)

        error = io.StringIO()
        with patch.object(policy_sync, "load_policy_documents", side_effect=ValueError("格式错误")), patch("builtins.__import__", side_effect=forbid_database), patch("sys.stderr", error):
            self.assertEqual(policy_sync.main(), 1)
        self.assertIn("格式错误", error.getvalue())
        self.assertIn("0 条", error.getvalue())

    def test_main_uses_fixed_path_and_explicit_creation(self):
        collection = FakeCollection()
        client = Mock()
        client.get_or_create_collection.return_value = collection
        chroma = SimpleNamespace(PersistentClient=Mock(return_value=client))
        with patch.dict(sys.modules, {"chromadb": chroma}), patch.object(policy_sync, "load_policy_documents", return_value=self.documents), patch("sys.stdout", io.StringIO()):
            self.assertEqual(policy_sync.main(), 0)
        expected = Path(policy_sync.__file__).resolve().parent / "chroma_db"
        chroma.PersistentClient.assert_called_once_with(path=str(expected))
        client.get_or_create_collection.assert_called_once_with(name="company_policy")

    def test_main_partial_failure_returns_nonzero(self):
        client = Mock()
        client.get_or_create_collection.return_value = FakeCollection(fail_id="hotel_002")
        chroma = SimpleNamespace(PersistentClient=Mock(return_value=client))
        error = io.StringIO()
        with patch.dict(sys.modules, {"chromadb": chroma}), patch.object(policy_sync, "load_policy_documents", return_value=self.documents), patch("sys.stderr", error):
            self.assertEqual(policy_sync.main(), 1)
        self.assertIn("已成功处理 1 条", error.getvalue())

    def test_import_no_side_effects(self):
        path = Path(policy_sync.__file__)
        spec = importlib.util.spec_from_file_location("isolated_policy_sync", path)
        module = importlib.util.module_from_spec(spec)
        original_import = builtins.__import__

        def restricted_import(name, *args, **kwargs):
            if name in ("main", "chromadb", "ollama", "sentence_transformers"):
                raise AssertionError("导入时访问外部资源")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=restricted_import), patch("policy_loader.load_policy_documents", side_effect=AssertionError("导入时读取政策")), patch("builtins.input", side_effect=AssertionError("禁止输入")):
            spec.loader.exec_module(module)
        self.assertTrue(callable(module.sync_policies))


if __name__ == "__main__":
    unittest.main()
