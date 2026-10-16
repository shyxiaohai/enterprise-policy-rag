"""用模拟 Streamlit/资源离线测试，不启动网页或数据库。"""

from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


class FakeStreamlit:
    def __init__(self):
        self.session_state = {}
        self.submitted = False
        self.question = ""
        self.events = []
        self.options = []

    def cache_resource(self, **kwargs):
        return lambda function: function

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.events.append((name, args))
            self.options.append((name, kwargs))
            if name in ("container", "expander", "form", "spinner"):
                return nullcontext()
            if name == "text_area":
                return self.question
            if name == "form_submit_button":
                return self.submitted
        return call


class AppTests(unittest.TestCase):
    def setUp(self):
        self.st = FakeStreamlit()
        spec = importlib.util.spec_from_file_location("isolated_app", Path(__file__).resolve().parent / "app.py")
        self.app = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"streamlit": self.st}):
            spec.loader.exec_module(self.app)
        self.policy = {"source": "company_policy.txt", "policy_id": "meal_001", "content": "真实政策", "distance": 0.1, "rerank_score": 0.8}
        self.result = {"status": "ok", "answer": "唯一回答", "message": "", "policies": [self.policy], "retrieved": [self.policy], "reranked": [self.policy], "final_policies": [self.policy]}

    def test_missing_path_never_initializes(self):
        with patch.dict(self.app.os.environ, {}, clear=True), patch.object(self.app, "get_resources") as resources:
            self.app.main()
        resources.assert_not_called()

    def test_invalid_path_no_fallback_or_path_leak(self):
        with patch.dict(self.app.os.environ, {self.app.TEST_DB_ENV: "test"}), patch.object(self.app, "validate_test_database", side_effect=ValueError("敏感路径")), patch.object(self.app, "get_resources") as resources:
            self.app.main()
        resources.assert_not_called()
        self.assertNotIn("敏感路径", str(self.st.events))

    def test_form_submit_and_rerun_do_not_repeat_call(self):
        service = Mock()
        service.answer_question.return_value = self.result
        with patch.dict(self.app.os.environ, {self.app.TEST_DB_ENV: "copy"}), patch.object(self.app, "validate_test_database", return_value=Path("copy")), patch.object(Path, "read_bytes", return_value=b"policy"), patch.object(self.app, "get_resources", return_value=service):
            self.app.main()
            service.answer_question.assert_not_called()
            self.st.submitted = True
            self.st.question = " 餐饮标准？ "
            self.app.main()
            service.answer_question.assert_called_once_with("餐饮标准？")
            self.st.submitted = False
            self.app.main()
            service.answer_question.assert_called_once()
        self.assertIn("latest_result", self.st.session_state)

    def test_empty_submit(self):
        self.st.submitted = True
        self.st.question = "  "
        service = Mock()
        with patch.dict(self.app.os.environ, {self.app.TEST_DB_ENV: "copy"}), patch.object(self.app, "validate_test_database", return_value=Path("copy")), patch.object(Path, "read_bytes", return_value=b"policy"), patch.object(self.app, "get_resources", return_value=service):
            self.app.main()
        service.answer_question.assert_not_called()

    def test_statuses_and_real_policy_rendering(self):
        for status in ("ok", "guarded", "needs_input", "no_policy", "error"):
            self.st.events.clear()
            self.app.render_result(dict(self.result, status=status, message="唯一回答"))
            displays = [args[0] for name, args in self.st.events if name in ("write", "warning", "info", "error") and args]
            self.assertEqual(displays.count("唯一回答"), 0 if status == "error" else 1)
            self.assertIn("真实政策", displays)
            self.assertTrue(any(name == "text" and "meal_001" in args[0] for name, args in self.st.events))

    def test_exception_is_sanitized(self):
        service = Mock()
        service.answer_question.side_effect = RuntimeError("敏感路径")
        result = self.app.submit_question(service, "问题")
        self.assertEqual(result["status"], "error")
        self.assertNotIn("敏感路径", str(result))
        shared = self.app.SharedService(service)
        with self.assertRaises(RuntimeError):
            shared.answer_question("问题")
        self.assertTrue(shared.lock.acquire(blocking=False))
        shared.lock.release()

    def test_initialization_only_gets_existing_collection(self):
        collection = Mock(spec=["query"])
        client = Mock(spec=["get_collection"])
        client.get_collection.return_value = collection
        chroma = Mock()
        chroma.PersistentClient.return_value = client
        encoder = Mock()
        ollama = Mock()
        with patch.dict(sys.modules, {"chromadb": chroma, "sentence_transformers": encoder, "ollama": ollama}), patch.object(self.app, "validate_test_database", return_value=Path("copy")), patch.object(self.app, "load_policy_documents", return_value=[]), patch.object(Path, "read_bytes", return_value=b"policy"):
            shared = self.app.get_resources("copy", b"policy")
        client.get_collection.assert_called_once_with(name="company_policy")
        self.assertIs(shared.service.collection, collection)
        self.assertIs(shared.service.chat, ollama.chat)
        collection.query.assert_not_called()

    def test_initialization_error_is_sanitized(self):
        with patch.dict(self.app.os.environ, {self.app.TEST_DB_ENV: "copy"}), patch.object(self.app, "validate_test_database", return_value=Path("copy")), patch.object(Path, "read_bytes", return_value=b"policy"), patch.object(self.app, "get_resources", side_effect=RuntimeError("敏感路径")):
            self.app.main()
        self.assertNotIn("敏感路径", str(self.st.events))
        self.assertTrue(any(name == "error" for name, _ in self.st.events))

    def test_generated_citations_preserved_but_separated(self):
        answer = "500元/天。\n【政策依据】\n模型自写来源及解释"
        self.app.render_result(dict(self.result, answer=answer))
        writes = [args[0] for name, args in self.st.events if name == "write"]
        self.assertEqual(writes.count(answer), 1)
        self.assertIn("真实政策", writes)
        captions = "\n".join(args[0] for name, args in self.st.events if name == "caption")
        self.assertIn("引用身份与原文校验不等于业务语义全部正确", captions)
        titles = [args[0] for name, args in self.st.events if name == "subheader"]
        self.assertIn("系统政策原文 · 本次提供的依据", titles)

    def test_stage_order_and_unmodified_scores(self):
        second = dict(self.policy, policy_id="hotel_001", distance=0.27, rerank_score=1.23456789)
        result = dict(self.result, retrieved=[self.policy, second], reranked=[second, self.policy], final_policies=[second])
        self.app.render_result(result)
        tables = [args[0] for name, args in self.st.events if name == "dataframe"]
        self.assertEqual([row["编号"] for row in tables[0]], ["meal_001", "hotel_001"])
        self.assertEqual([row["编号"] for row in tables[1]], ["hotel_001", "meal_001"])
        self.assertEqual([row["编号"] for row in tables[2]], ["hotel_001"])
        self.assertEqual(tables[1][0]["Rerank 分数"], 1.23456789)
        self.assertEqual(tables[0][1]["Chroma 距离"], 0.27)
        self.assertEqual(tables[1][0]["政策正文"], second["content"])
        for name, options in self.st.options:
            if name == "dataframe":
                self.assertEqual(options["width"], "stretch")
                self.assertEqual(options["row_height"], 72)

    def test_final_candidates_are_not_rendered_as_answer_references(self):
        unrelated = dict(self.policy, policy_id="hotel_001", content="无关住宿原文")
        self.app.render_result(dict(self.result, final_policies=[unrelated]))
        writes = [args[0] for name, args in self.st.events if name == "write"]
        self.assertIn("真实政策", writes)
        self.assertNotIn("无关住宿原文", writes)
        self.assertTrue(all(not opts.get('expanded', False) for name, opts in self.st.options if name == 'expander'))

    def test_python_fallback_label_and_discarded_body_not_displayed(self):
        self.app.render_result(dict(self.result, diagnostics={
            'answer_origin': 'python_original_fallback', 'model_answer_discarded': True,
            'raw_model_answer': '不可信模型正文'}))
        captions = str([args for name, args in self.st.events if name == 'caption'])
        self.assertIn('政策原文回答', captions)
        self.assertNotIn('不可信模型正文', str(self.st.events))

    def test_paths_redacted_without_changing_results_or_scores(self):
        from copy import deepcopy
        result = dict(self.result, answer=r"政策文件 C:\private\secret.txt", policies=[
            dict(self.policy, source=r"C:\private\company_policy.txt")])
        before = deepcopy(result)
        self.app.render_result(result)
        self.assertNotIn(r'C:\private', str(self.st.events))
        self.assertEqual(result, before)
        self.assertIn('本地路径已隐藏', str(self.st.events))

    def test_chinese_title_examples_and_known_limitation_without_resources(self):
        with patch.dict(self.app.os.environ, {}, clear=True):
            self.app.main()
        self.assertIn(('title', ('企业AI智能知识库',)), self.st.events)
        self.assertTrue(any('H09' in str(args) for _, args in self.st.events))
        self.assertEqual(len(self.app.EXAMPLES), 4)

    def test_light_theme_and_semantic_style_selectors(self):
        config = (Path(__file__).resolve().parent / '.streamlit' / 'config.toml').read_text(encoding='utf-8')
        self.assertIn('base = "light"', config)
        self.assertIn('backgroundColor = "#f6f8fc"', config)
        self.assertIn('textColor = "#1e293b"', config)
        css = self.app.APP_STYLES
        self.assertNotIn('.stApp', css)
        self.assertNotIn('.block-container', css)
        self.assertNotIn('data-testid', css)
        for selector in ('main h1', 'main label', 'main textarea', '::placeholder',
                         ':focus', 'button:hover', ':focus-visible', '[role="alert"]'):
            self.assertIn(selector, css)

    def test_style_palette_body_and_helper_text_contrast(self):
        def luminance(value):
            rgb = [int(value[i:i+2], 16) / 255 for i in (1, 3, 5)]
            rgb = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
            return sum(v * weight for v, weight in zip(rgb, (.2126, .7152, .0722)))
        for foreground, background in (('#183153', '#f6f8fc'), ('#1e293b', '#ffffff'),
                                       ('#334155', '#f6f8fc'), ('#475569', '#ffffff'),
                                       ('#ffffff', '#1d4ed8')):
            values = sorted((luminance(foreground), luminance(background)))
            self.assertGreaterEqual((values[1] + .05) / (values[0] + .05), 4.5)


if __name__ == "__main__":
    unittest.main()
