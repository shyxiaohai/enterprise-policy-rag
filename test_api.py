"""接口离线测试：生命周期注入假服务，不打开数据库或加载模型。"""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from api import create_app, configured_service
from service_resources import SharedService


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.policy = {"policy_id": "hotel_002", "source": "company_policy.txt",
                       "content": "二线城市出差住宿报销标准为400元/天。",
                       "distance": 0.1, "rerank_score": 0.8}
        self.result = {"status": "ok", "answer": "标准400元/天", "message": "",
                       "policies": [self.policy], "final_policies": [{"content": "不可公开候选"}],
                       "retrieved": [], "reranked": [], "subquery_retrievals": [],
                       "diagnostics": {"private": "不可公开诊断"}}
        self.service = Mock()
        self.service.answer_question.return_value = self.result
        self.factory = Mock(return_value=self.service)
        self.app = create_app(self.factory)

    def test_health_no_generation_and_single_initialization(self):
        self.factory.assert_not_called()
        with TestClient(self.app) as client:
            for _ in range(2):
                response = client.get("/health")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"status": "ok", "service_initialized": True})
            self.factory.assert_called_once_with()
            self.service.answer_question.assert_not_called()
        self.assertIsNone(self.app.state.service)

    def test_question_trim_and_public_fields_only(self):
        before = deepcopy(self.result)
        with TestClient(self.app) as client:
            response = client.post("/ask", json={"question": " 二线城市住宿？ "})
        self.assertEqual(response.status_code, 200)
        self.service.answer_question.assert_called_once_with("二线城市住宿？")
        body = response.json()
        self.assertEqual(set(body), {"status", "answer", "policies", "message"})
        self.assertEqual(body["policies"], [{k: self.policy[k] for k in ("policy_id", "source", "content")}])
        self.assertNotIn("不可公开", response.text)
        self.assertEqual(self.result, before)

    def test_invalid_inputs_never_call_service(self):
        invalid = [{}, {"question": " \n "}, {"question": 1}, {"question": True},
                   {"question": None}, {"question": []}, {"question": {}},
                   {"question": "问", "extra": "private"}, {"question": "问" * 2001}]
        with TestClient(self.app) as client:
            for payload in invalid:
                with self.subTest(payload_type=str(type(payload.get("question")))):
                    response = client.post("/ask", json=payload)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(set(response.json()), {"status", "answer", "policies", "message"})
            self.assertEqual(client.post("/ask", content="{", headers={"content-type": "application/json"}).status_code, 422)
        self.service.answer_question.assert_not_called()

    def test_maximum_length_after_strip(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.post("/ask", json={"question": " " + "问" * 2000 + " "}).status_code, 200)
        self.service.answer_question.assert_called_once_with("问" * 2000)

    def test_business_statuses(self):
        with TestClient(self.app) as client:
            for status in ("ok", "guarded", "needs_input", "no_policy"):
                self.service.answer_question.return_value = dict(self.result, status=status)
                response = client.post("/ask", json={"question": "问题"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], status)

    def test_core_error_is_generic_503(self):
        self.service.answer_question.return_value = dict(self.result, status="error", message="PRIVATE_EXCEPTION")
        with TestClient(self.app) as client:
            response = client.post("/ask", json={"question": "问题"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("PRIVATE_EXCEPTION", response.text)
        self.assertEqual(response.json()["policies"], [])

    def test_unexpected_exception_is_generic_500(self):
        self.service.answer_question.side_effect = RuntimeError("PRIVATE_EXCEPTION")
        with TestClient(self.app) as client:
            response = client.post("/ask", json={"question": "问题"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("PRIVATE_EXCEPTION", response.text)

    def test_invalid_core_response_is_generic_500(self):
        with TestClient(self.app) as client:
            for result in ({}, dict(self.result, status="invalid"), dict(self.result, policies=None)):
                self.service.answer_question.return_value = result
                self.assertEqual(client.post("/ask", json={"question": "问题"}).status_code, 500)

    def test_failed_startup_is_unavailable_without_leaking_exception(self):
        app = create_app(Mock(side_effect=RuntimeError("PRIVATE_EXCEPTION")))
        with TestClient(app) as client:
            response = client.get("/health")
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json()["service_initialized"])
            response = client.post("/ask", json={"question": "问题"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("PRIVATE_EXCEPTION", response.text)

    def test_paths_redacted_in_all_public_strings(self):
        path = '"C:\\private folder\\secret.txt"'
        self.service.answer_question.return_value = dict(self.result, answer=path, message="/private/secret.txt",
            policies=[dict(self.policy, source=path, content=r"\\server\share\secret.txt", policy_id=path)])
        with TestClient(self.app) as client:
            response = client.post("/ask", json={"question": "问题"})
        self.assertEqual(response.status_code, 200)
        for marker in ("private", "secret.txt", "server"):
            self.assertNotIn(marker, response.text)

    def test_configuration_required_and_factory_does_not_fallback(self):
        with patch("api.os.environ", {}), patch("api.build_service") as build:
            with self.assertRaises(ValueError):
                configured_service()
            build.assert_not_called()
        with patch("api.os.environ", {"POLICY_API_TEST_DB_PATH": "copy"}), patch("api.build_service") as build:
            configured_service()
            build.assert_called_once_with("copy")

    def test_injected_factory_bypasses_real_resource_loading(self):
        with patch("api.build_service", side_effect=AssertionError("real resources forbidden")):
            with TestClient(self.app) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.post("/ask", json={"question": "问题"}).status_code, 200)

    def test_shared_lock_serializes_calls_and_releases_after_exception(self):
        entered, release, second_started = Event(), Event(), Event()
        calls = []

        def answer(question):
            calls.append(question)
            if question == "first":
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test timed out")
            return question

        shared = SharedService(Mock(answer_question=answer))

        def second_call():
            second_started.set()
            return shared.answer_question("second")

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(shared.answer_question, "first")
            try:
                self.assertTrue(entered.wait(3))
                second = pool.submit(second_call)
                self.assertTrue(second_started.wait(3))
                self.assertEqual(calls, ["first"])
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3), "first")
            self.assertEqual(second.result(timeout=3), "second")
        shared.service.answer_question = Mock(side_effect=RuntimeError("failure"))
        with self.assertRaises(RuntimeError):
            shared.answer_question("third")
        self.assertTrue(shared.lock.acquire(blocking=False))
        shared.lock.release()


if __name__ == "__main__":
    unittest.main()
