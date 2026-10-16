"""CLI 模拟测试，不访问正式数据库或模型。"""
import builtins
from contextlib import ExitStack
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import main as cli
from policy_loader import load_policy_documents
from rag_service import RagService


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self.documents = load_policy_documents()
        self.collection = Mock(spec=["query"])
        self.client = Mock(spec=["get_collection"])
        self.client.get_collection.return_value = self.collection
        self.chroma = SimpleNamespace(PersistentClient=Mock(return_value=self.client))
        self.chat = Mock(return_value={"message": {"content": "唯一答案"}})
        self.encoder = Mock()
        self.encoder.predict.return_value = [1.0]
        self.cross_encoder = Mock(return_value=self.encoder)

    def run_cli(self, inputs, service=None, parse_error=None, argv=None, real_paths=False):
        output, errors = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {
                "chromadb": self.chroma, "ollama": SimpleNamespace(chat=self.chat),
                "sentence_transformers": SimpleNamespace(CrossEncoder=self.cross_encoder)
            }))
            loader = stack.enter_context(patch.object(cli, "load_policy_documents", return_value=self.documents, side_effect=parse_error))
            if not real_paths:
                stack.enter_context(patch.object(Path, "is_dir", return_value=True))
                stack.enter_context(patch.object(Path, "is_file", return_value=True))
            if service is not None:
                factory = stack.enter_context(patch.object(cli, "RagService", return_value=service))
            else:
                factory = None
            input_mock = stack.enter_context(patch("builtins.input", side_effect=inputs))
            stack.enter_context(patch("sys.stdout", output))
            stack.enter_context(patch("sys.stderr", errors))
            code = cli.main([] if argv is None else argv)
        return code, output.getvalue(), errors.getvalue(), input_mock, factory

    def test_single_answer_and_read_only_initialization(self):
        service = Mock()
        service.answer_question.return_value = {"status": "ok", "answer": "唯一答案", "message": ""}
        code, output, _, _, factory = self.run_cli(["问题", "quit"], service)
        self.assertEqual(code, 0)
        service.answer_question.assert_called_once_with("问题")
        self.assertEqual(output.count("唯一答案"), 1)
        self.chroma.PersistentClient.assert_called_once_with(path=str(cli.DATABASE_PATH))
        self.client.get_collection.assert_called_once_with(name="company_policy")
        factory.assert_called_once_with(self.collection, self.encoder, self.documents, chat=self.chat)
        from policy_sync import DATABASE_PATH
        self.assertEqual(cli.DATABASE_PATH, DATABASE_PATH)

    def test_quit_does_not_ask_service(self):
        service = Mock()
        self.run_cli(["quit"], service)
        service.answer_question.assert_not_called()

    def test_parse_failure_prevents_database(self):
        code, _, errors, inputs, _ = self.run_cli([], parse_error=ValueError("格式错误"))
        self.assertEqual(code, 1)
        self.assertIn("政策解析失败", errors)
        self.chroma.PersistentClient.assert_not_called()
        inputs.assert_not_called()

    def test_missing_collection_stops(self):
        self.client.get_collection.side_effect = RuntimeError("不存在")
        code, _, errors, inputs, _ = self.run_cli([])
        self.assertEqual(code, 1)
        self.assertIn("集合", errors)
        inputs.assert_not_called()
        self.cross_encoder.assert_not_called()

    def test_missing_database_stops(self):
        with patch.object(cli, "load_policy_documents", return_value=self.documents), patch.object(Path, "is_dir", return_value=False), patch("builtins.input") as inputs, patch("sys.stderr", io.StringIO()):
            self.assertEqual(cli.main([]), 1)
        inputs.assert_not_called()

    def test_real_service_with_mock_model_recovers(self):
        meal = next(p for p in self.documents if p["policy_id"] == "meal_001")
        self.collection.query.return_value = {"documents": [[meal["content"]]], "metadatas": [[meal]], "distances": [[0.1]]}
        from policy_citations import render_trusted_answer
        self.chat.side_effect = [RuntimeError("内部错误"), {"message": {"content": render_trusted_answer("恢复回答", [meal])}}]
        code, output, _, _, _ = self.run_cli(["餐饮标准？", "餐饮标准？", "quit"])
        self.assertEqual(code, 0)
        self.assertEqual(output.count("模型调用失败"), 1)
        self.assertEqual(output.count("恢复回答"), 1)
        self.assertEqual(self.chat.call_count, 2)

    def test_non_model_error_is_not_no_policy(self):
        service = Mock()
        service.answer_question.side_effect = [RuntimeError("数据库故障"), {"status": "ok", "answer": "恢复回答", "message": ""}]
        code, output, _, _, _ = self.run_cli(["问题", "问题", "quit"], service)
        self.assertEqual(code, 0)
        self.assertIn("检索或重排序", output)
        self.assertNotIn("未找到", output)
        self.assertEqual(output.count("恢复回答"), 1)

    def test_status_messages_print_once(self):
        for status in ("needs_input", "no_policy", "error", "guarded"):
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli.print_result({"status": status, "answer": "唯一提示", "message": "唯一提示"})
            self.assertEqual(output.getvalue().count("唯一提示"), 1)

    def test_import_no_side_effects(self):
        spec = importlib.util.spec_from_file_location("isolated_cli", Path(cli.__file__))
        module = importlib.util.module_from_spec(spec)
        original_import = builtins.__import__

        def checked_import(name, *args, **kwargs):
            if name in ("chromadb", "ollama", "sentence_transformers"):
                raise AssertionError("导入时加载外部资源")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=checked_import), patch("builtins.input", side_effect=AssertionError("禁止输入")), patch("policy_loader.load_policy_documents", side_effect=AssertionError("禁止加载政策")), patch("rag_service.RagService", side_effect=AssertionError("禁止创建服务")):
            spec.loader.exec_module(module)
        self.assertTrue(callable(module.main))

    def test_valid_test_path_and_missing_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            formal.mkdir()
            (formal / "chroma.sqlite3").write_bytes(b"formal placeholder")
            copy = root / "copy"
            copy.mkdir()
            (copy / "chroma.sqlite3").write_bytes(b"test placeholder")
            with patch.object(cli, "DATABASE_PATH", formal):
                code, output, _, _, _ = self.run_cli(["quit"], argv=["--test-db-path", str(copy)], real_paths=True)
                self.assertEqual(code, 0)
                self.assertIn("副本测试模式", output)
                self.assertIn(str(copy.resolve()), output)
                self.chroma.PersistentClient.assert_called_once_with(path=str(copy.resolve()))
                self.client.get_collection.assert_called_once_with(name="company_policy")
                self.chroma.PersistentClient.reset_mock()
                self.client.get_collection.side_effect = RuntimeError("missing")
                code, _, _, inputs, _ = self.run_cli([], argv=["--test-db-path", str(copy)], real_paths=True)
                self.assertEqual(code, 1)
                inputs.assert_not_called()

    def test_unsafe_paths_fail_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            formal.mkdir()
            (formal / "chroma.sqlite3").write_bytes(b"placeholder")
            empty = root / "empty"
            empty.mkdir()
            child = formal / "child"
            child.mkdir()
            with patch.object(cli, "DATABASE_PATH", formal):
                for path in (formal, formal / ".." / "formal", empty, root / "missing", child):
                    with self.subTest(path=path):
                        code, _, errors, inputs, _ = self.run_cli([], argv=["--test-db-path", str(path)], real_paths=True)
                        self.assertEqual(code, 1)
                        self.assertIn("安全校验失败", errors)
                        self.chroma.PersistentClient.assert_not_called()
                        inputs.assert_not_called()

    def test_hardlinked_database_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal, copy = root / "formal", root / "copy"
            formal.mkdir()
            copy.mkdir()
            (formal / "chroma.sqlite3").write_bytes(b"placeholder")
            os.link(str(formal / "chroma.sqlite3"), str(copy / "chroma.sqlite3"))
            with patch.object(cli, "DATABASE_PATH", formal):
                with self.assertRaises(ValueError):
                    cli.validate_test_database(copy)

    def test_link_and_uncertain_platform_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            # 模拟 lstat，覆盖 Windows 联接及无重解析点信息的情况。
            for info in (
                SimpleNamespace(st_mode=0o120777, st_nlink=1, st_file_attributes=0),
                SimpleNamespace(st_mode=0o40755, st_nlink=1, st_file_attributes=0x400),
                SimpleNamespace(st_mode=0o40755, st_nlink=1)
            ):
                with patch.object(cli.os, "name", "nt"), patch.object(Path, "lstat", return_value=info):
                    with self.assertRaises(ValueError):
                        cli.validate_test_database(path)

    def test_link_inside_copy_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "chroma.sqlite3").write_bytes(b"placeholder")
            (root / "index").write_bytes(b"placeholder")
            original = Path.lstat

            def fake_lstat(path):
                if path.name == "index":
                    return SimpleNamespace(st_mode=0o120777, st_nlink=1, st_file_attributes=0)
                return original(path)

            with patch.object(Path, "lstat", fake_lstat):
                with self.assertRaises(ValueError):
                    cli.validate_test_database(root)

    def test_argument_missing_value(self):
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as caught:
            cli.main(["--test-db-path"])
        self.assertEqual(caught.exception.code, 2)
        self.chroma.PersistentClient.assert_not_called()


if __name__ == "__main__":
    unittest.main()
