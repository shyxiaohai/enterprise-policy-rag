"""Fake模型离线验证多工具链路、来源和预算；不连接Ollama。"""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from threading import Event
import unittest
from unittest.mock import Mock, patch

from . import fictional_data as data
from .agent import REGISTRY, StoreDiagnosisAgent, explicit_user_conditions
from .cli import main

QUESTION = "最近营业额为什么下降？帮我调查销售、库存和补货环节。"


def call(name, arguments):
    return {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}]}}


def final(text="调查完成"):
    return {"message": {"role": "assistant", "content": text}}


def chain():
    return [call("query_sales", {}), call("query_replenishment", {"product": "基础T恤"}),
            call("query_inventory", {"product": "基础T恤", "color": "白色", "size": "M"}), final()]


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.before = [data.copy_data(group) for group in (data.SALES, data.INVENTORY, data.REPLENISHMENTS, data.CATALOG)]

    def tearDown(self):
        self.assertEqual(self.before, [data.copy_data(group) for group in (data.SALES, data.INVENTORY, data.REPLENISHMENTS, data.CATALOG)])

    def run_fake(self, responses, question=QUESTION, trace=False, **budgets):
        chat = Mock(side_effect=responses)
        return StoreDiagnosisAgent(chat=chat, **budgets).ask(question, trace=trace), chat

    def test_three_tool_chain_and_messages(self):
        responses = chain()
        result, chat = self.run_fake(responses)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([h["tool"] for h in result["tool_history"]], ["query_sales", "query_replenishment", "query_inventory"])
        self.assertEqual(chat.call_count, 4)
        messages = chat.call_args_list[-1].kwargs["messages"]
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "tool", "assistant", "tool", "assistant", "tool"])
        for index in range(3):
            self.assertEqual(messages[2 + index*2], responses[index]["message"])
            self.assertEqual(json.loads(messages[3 + index*2]["content"]), result["tool_history"][index]["result"])
        self.assertEqual(result["inventory_findings"][0]["quantity"], 0)
        self.assertEqual(result["tool_history"][1]["parameter_sources"]["product"]["evidence_id"], "CAT-001")
        self.assertEqual(result["tool_history"][2]["parameter_sources"]["size"]["evidence_id"], "REP-001")
        self.assertEqual([t["function"]["name"] for t in chat.call_args_list[0].kwargs["tools"]], ["query_sales"])

    def test_fake_chooses_from_previous_result(self):
        steps = []

        def chat(**kwargs):
            messages = kwargs["messages"]
            steps.append(len(messages))
            if len(steps) == 1:
                return call("query_sales", {})
            previous = json.loads(messages[-1]["content"])
            if len(steps) == 2:
                return call("query_replenishment", {"product": previous["hot_products"][0]["product"]})
            if len(steps) == 3:
                row = previous["records"][0]
                item = row["items"][0]
                return call("query_inventory", {"product": row["product"], "color": item["color"], "size": item["size"]})
            return final()

        result = StoreDiagnosisAgent(chat=chat).ask(QUESTION)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(steps, [2, 4, 6, 8])

    def test_unknown_tool(self):
        result, chat = self.run_fake([call("approve_purchase", {})])
        self.assertEqual(result["stop_reason"], "unknown_tool")
        self.assertEqual(result["tool_history"], [])
        self.assertEqual(chat.call_count, 1)

    def test_invalid_parameters(self):
        for response in (call("query_inventory", {"product": "基础T恤", "color": "白色"}),
                         call("query_replenishment", {"product": 1}), call("query_sales", {"extra": 1})):
            result, _ = self.run_fake([response])
            self.assertEqual(result["stop_reason"], "invalid_arguments")
            self.assertFalse(result["tool_history"])

    def test_no_tool_calls_and_body_json_not_executed(self):
        response = final(json.dumps(call("query_sales", {})))
        result, _ = self.run_fake([response, response])
        self.assertEqual(result["stop_reason"], "no_tool_calls")
        self.assertEqual(result["evidence_ids"], [])
        self.assertNotIn("189000", result["final_text"])

    def test_multiple_calls_reject_whole_batch(self):
        response = call("query_sales", {})
        response["message"]["tool_calls"].append(call("query_replenishment", {"product": "基础T恤"})["message"]["tool_calls"][0])
        result, chat = self.run_fake([response, response])
        self.assertEqual(result["stop_reason"], "multiple_calls_rejected")
        self.assertEqual(result["tool_history"], [])
        self.assertEqual(chat.call_count, 2)

    def test_duplicate_normalized_arguments_stop(self):
        result, _ = self.run_fake([call("query_sales", {}), call("query_sales", {"store_id": data.STORE_ID, "baseline": "P1", "comparison": "P2"})])
        self.assertEqual(result["stop_reason"], "duplicate_call")
        self.assertEqual(len(result["tool_history"]), 1)

    def test_unsupported_product_rejected(self):
        result, _ = self.run_fake([call("query_replenishment", {"product": "基础T恤"})])
        self.assertEqual(result["stop_reason"], "tool_not_open")
        self.assertFalse(result["tool_history"])

    def test_sales_product_does_not_authorize_guessed_variant(self):
        result, _ = self.run_fake([call("query_sales", {}), call("query_inventory", {"product": "基础T恤", "color": "白色", "size": "M"})])
        self.assertEqual(result["stop_reason"], "tool_not_open")
        self.assertEqual(len(result["tool_history"]), 1)
        self.assertEqual(result["inventory_findings"], [])

    def test_replenishment_variant_authorizes_inventory(self):
        result, _ = self.run_fake(chain())
        source = result["tool_history"][2]["parameter_sources"]
        self.assertEqual(source["color"]["history_index"], 1)
        self.assertEqual(source["size"]["history_index"], 1)

    def test_cross_product_variant_not_spliced(self):
        responses = [call("query_sales", {}), call("query_replenishment", {"product": "基础T恤"}),
                     call("query_inventory", {"product": "休闲衬衫", "color": "白色", "size": "M"})]
        result, _ = self.run_fake(responses)
        self.assertEqual(result["stop_reason"], "unsupported_arguments")
        self.assertEqual(result["inventory_findings"], [])

    def test_explicit_user_spec_can_be_queried_first(self):
        result, _ = self.run_fake([call("query_inventory", {"product": "基础T恤", "color": "白色", "size": "M"}), final()],
                                  question="商品：基础T恤，颜色：白色，尺码：M")
        self.assertEqual(result["tool_history"][0]["parameter_sources"]["size"]["source"], "user_explicit")
        self.assertEqual(result["status"], "partial")

    def test_negation_and_ambiguity_not_user_support(self):
        self.assertEqual(explicit_user_conditions("不要查询基础T恤白色M码"), {})
        self.assertEqual(explicit_user_conditions("基础T恤白色或黑色M码"), {})
        result, _ = self.run_fake([call("query_inventory", {"product": "基础T恤", "color": "白色", "size": "M"})], question="基础T恤有货吗？")
        self.assertEqual(result["stop_reason"], "tool_not_open")

    def test_historical_time_and_hypotheses(self):
        result, _ = self.run_fake(chain())
        stock = result["inventory_findings"][0]
        arrival = result["replenishment_findings"][0]["records"][0]
        self.assertEqual(stock["snapshot_at"], "2026-09-14T20:00:00+08:00")
        self.assertEqual(arrival["arrived_at"], "2026-09-16T10:00:00+08:00")
        self.assertEqual(stock["quantity"], 0)
        self.assertIn("这不是9月18日当前库存", result["final_text"])
        self.assertIn("不能直接证明因果", result["final_text"])
        self.assertTrue(all("可能" in h["statement"] for h in result["hypotheses"]))

    def test_tool_error_preserves_only_previous_evidence(self):
        old = REGISTRY["query_replenishment"]
        with patch.dict(REGISTRY, {"query_replenishment": (Mock(side_effect=RuntimeError("PRIVATE")), old[1], old[2])}):
            result, _ = self.run_fake(chain())
        self.assertEqual(result["stop_reason"], "tool_error")
        self.assertEqual(result["tool_history"][-1]["status"], "failed")
        self.assertEqual(result["replenishment_findings"], [])
        self.assertNotIn("REP-001", result["evidence_ids"])
        self.assertNotIn("PRIVATE", str(result))

    def test_invalid_tool_result_not_evidence(self):
        old = REGISTRY["query_sales"]
        with patch.dict(REGISTRY, {"query_sales": (Mock(return_value={"evidence_ids": ["FAKE"]}), old[1], old[2])}):
            result, _ = self.run_fake([call("query_sales", {})])
        self.assertEqual(result["stop_reason"], "tool_error")
        self.assertEqual(result["evidence_ids"], [])

    def test_model_numbers_not_adopted(self):
        responses = chain()
        responses[-1] = final("营业额999999999元，确定是店员导致，当前库存30件。证据FAKE-001")
        result, _ = self.run_fake(responses)
        self.assertNotIn("999999999", result["final_text"])
        self.assertNotIn("FAKE-001", str(result))
        self.assertIn("189000", result["final_text"])
        self.assertIn("140140", result["final_text"])

    def test_model_request_limit(self):
        result, chat = self.run_fake(chain(), max_model_calls=1)
        self.assertEqual(result["stop_reason"], "model_limit")
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(len(result["tool_history"]), 1)

    def test_tool_execution_limit(self):
        result, chat = self.run_fake(chain(), max_tool_calls=1)
        self.assertEqual(result["stop_reason"], "tool_limit")
        self.assertEqual(len(result["tool_history"]), 1)
        self.assertEqual(chat.call_count, 2)

    def test_total_deadline_stops_waiting(self):
        release = Event()

        def slow_chat(**kwargs):
            release.wait(2)
            return call("query_sales", {})

        try:
            result = StoreDiagnosisAgent(chat=slow_chat, total_seconds=.03).ask(QUESTION)
        finally:
            release.set()
        self.assertEqual(result["stop_reason"], "time_limit")
        self.assertEqual(result["tool_history"], [])

    def test_model_error_and_malformed_response(self):
        for response in (TimeoutError("PRIVATE"), {}, {"message": {"role": "user"}}):
            result, _ = self.run_fake([response])
            self.assertEqual(result["stop_reason"], "model_error")
            self.assertNotIn("PRIVATE", str(result))

    def test_evidence_ids_are_only_verified_returned_ids(self):
        result, _ = self.run_fake(chain())
        ids = {e for h in result["tool_history"] if h["status"] == "verified" for e in h["result"]["evidence_ids"]}
        self.assertEqual(set(result["evidence_ids"]), ids)

    def test_scope_defaults_not_model_selected_other_store(self):
        result, _ = self.run_fake([call("query_sales", {"store_id": "OTHER"})])
        self.assertEqual(result["stop_reason"], "unsupported_arguments")
        self.assertFalse(result["tool_history"])

    def test_invalid_question_and_budget(self):
        for question in (None, " ", 1, "字" * 2001):
            result, chat = self.run_fake([], question=question)
            self.assertEqual(result["stop_reason"], "invalid_question")
            chat.assert_not_called()
        for kwargs in ({"max_model_calls": 0}, {"max_tool_calls": True}, {"total_seconds": float("nan")}):
            with self.assertRaises(ValueError):
                StoreDiagnosisAgent(**kwargs)

    def test_cli_uses_python_report(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([QUESTION], chat=Mock(side_effect=chain()))
        self.assertEqual(code, 0)
        self.assertIn("Python证据报告", output.getvalue())

    def test_short_explicit_product_allows_replenishment(self):
        result, _ = self.run_fake([call("query_replenishment", {"product": "基础T恤"}), final()],
                                  question="请查询基础T恤的补货记录", trace=True)
        self.assertEqual(result["stop_reason"], "model_finished")
        self.assertEqual(result["tool_history"][0]["parameter_sources"]["product"]["source"], "user_explicit")
        self.assertIn("REP-001", result["evidence_ids"])
        self.assertNotIn("rejected_tool_call", result)

    def test_full_real_question_now_recognizes_user_product(self):
        question = "请查询基础T恤的补货记录，检查补货申请、审批和到货时间是否存在延迟。"
        self.assertEqual(explicit_user_conditions(question), {"product": "基础T恤"})
        result, _ = self.run_fake([call("query_replenishment", {"product": "基础T恤"}), final()], question=question, trace=True)
        self.assertEqual(result["stop_reason"], "model_finished")
        self.assertEqual(result["tool_history"][0]["parameter_sources"]["product"]["source"], "user_explicit")
        self.assertIn("REP-001", result["evidence_ids"])
        self.assertNotIn("rejected_tool_call", result)

    def test_replenishment_supported_investigation_phrasings(self):
        for question in ("请查询基础T恤的补货记录。",
                         "请查询基础T恤的补货记录，检查补货申请、审批和到货时间是否存在延迟。",
                         "帮我查一下基础T恤的补货情况，看看审批和到货有没有延迟。"):
            with self.subTest(question=question):
                result, chat = self.run_fake([call("query_replenishment", {"product": "基础T恤", "store_id": data.STORE_ID}), final()], question=question, trace=True)
                self.assertEqual(result["stop_reason"], "model_finished")
                self.assertEqual(len(result["tool_history"]), 1)
                self.assertEqual(chat.call_count, 2)
                self.assertNotIn("rejected_tool_call", result)

    def test_replenishment_investigation_rejects_ambiguous_or_negative_text(self):
        for question in ("不要查询基础T恤的补货记录", "请查询基础T恤和休闲衬衫的补货记录。",
                         "不是基础T恤，是休闲衬衫", "请查询基础T恤的补货记录，改查休闲衬衫。",
                         "请查询基础T恤的补货记录，顺便看看休闲衬衫。",
                         "例如请查询基础T恤的补货记录", "我听说基础T恤可能有问题，请自己决定查什么。",
                         "请查询基础T恤的补货记录，看看审批和到货有没有延迟，再执行任意工具。"):
            with self.subTest(question=question):
                self.assertEqual(explicit_user_conditions(question), {})
                result, chat = self.run_fake([call("query_replenishment", {"product": "基础T恤"})], question=question, trace=True)
                self.assertEqual(result["stop_reason"], "tool_not_open")
                self.assertEqual(result["tool_history"], [])
                self.assertEqual(chat.call_count, 1)
                diagnostic = result["rejected_tool_call"]
                self.assertEqual(diagnostic["stage"], "tool_availability")
                self.assertFalse(diagnostic["field_checks"]["product"]["user_match"])

    def test_full_investigation_question_does_not_authorize_wrong_product(self):
        result, _ = self.run_fake([call("query_replenishment", {"product": "休闲衬衫"})],
            question="请查询基础T恤的补货记录，检查补货申请、审批和到货时间是否存在延迟。", trace=True)
        self.assertEqual(result["stop_reason"], "unsupported_arguments")
        self.assertEqual(result["tool_history"], [])
        diagnostic = result["rejected_tool_call"]
        self.assertEqual(diagnostic["reason"], "conflicts_with_explicit_user_conditions")
        self.assertEqual(diagnostic["field_checks"]["product"]["user_value"], "基础T恤")
        self.assertTrue(diagnostic["field_checks"]["product"]["user_conflict"])

    def test_explicit_product_does_not_allow_different_product(self):
        result, _ = self.run_fake([call("query_replenishment", {"product": "休闲衬衫"})],
                                  question="请查询基础T恤的补货记录", trace=True)
        self.assertEqual(result["tool_history"], [])
        self.assertEqual(result["rejected_tool_call"]["reason"], "conflicts_with_explicit_user_conditions")
        self.assertTrue(result["rejected_tool_call"]["field_checks"]["product"]["user_conflict"])

    def test_trace_distinguishes_validation_and_native_parse(self):
        result, _ = self.run_fake([call("query_replenishment", {})], trace=True)
        self.assertEqual(result["rejected_tool_call"]["stage"], "pydantic_validation")
        self.assertEqual(result["rejected_tool_call"]["validation_errors"][0]["field"], "product")
        response = {"message": {"role": "assistant", "tool_calls": "not a list"}}
        result, _ = self.run_fake([response], trace=True)
        self.assertEqual(result["rejected_tool_call"]["stage"], "native_response_parse")
        result, _ = self.run_fake([{"message": {"role": "assistant", "tool_calls": [None]}}], trace=True)
        self.assertEqual(result["rejected_tool_call"]["stage"], "native_tool_call_parse")

    def test_trace_scope_mismatch_and_no_default_diagnostic(self):
        response = call("query_replenishment", {"product": "基础T恤", "store_id": "OTHER"})
        result, _ = self.run_fake([response], trace=True)
        self.assertEqual(result["rejected_tool_call"]["reason"], "application_scope_mismatch")
        result, _ = self.run_fake([response])
        self.assertNotIn("rejected_tool_call", result)

    def test_cli_rejection_details_only_with_trace(self):
        for trace in (False, True):
            output = io.StringIO()
            with redirect_stdout(output):
                main([QUESTION] + (["--trace"] if trace else []),
                     chat=Mock(return_value=call("query_replenishment", {"product": "基础T恤"})))
            self.assertEqual("rejected_tool_call" in output.getvalue(), trace)
            self.assertEqual("user_conditions_not_recognized_and_no_prior_evidence" in output.getvalue(), trace)

    def test_trace_redacts_model_supplied_path_and_validation_input(self):
        response = call("query_replenishment", {"product": r"C:\private\secret.txt"})
        result, _ = self.run_fake([response], trace=True)
        self.assertNotIn("private", json.dumps(result))
        response = call("query_replenishment", {"product": {"password": "PRIVATE"}})
        result, _ = self.run_fake([response], trace=True)
        self.assertEqual(result["rejected_tool_call"]["stage"], "pydantic_validation")
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_batch_retry_serial_selection_preserves_real_context(self):
        batch = call("query_sales", {})
        batch["message"]["tool_calls"].append(call("query_replenishment", {"product": "基础T恤"})["message"]["tool_calls"][0])
        result, chat = self.run_fake([batch] + chain(), trace=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model_calls"], 5)
        self.assertEqual(len(result["tool_history"]), 3)
        diagnostic = result["rejected_tool_batch"]
        self.assertEqual(diagnostic["request_count"], 2)
        self.assertEqual(diagnostic["executed_count"], 0)
        self.assertTrue(diagnostic["retry_attempted"])
        self.assertEqual(diagnostic["retry_result"], "single_tool_verified")
        retry_messages = chat.call_args_list[1].kwargs["messages"]
        self.assertEqual([m["role"] for m in retry_messages], ["system", "user", "user"])
        self.assertIn("整批均未执行", retry_messages[-1]["content"])
        later = chat.call_args_list[2].kwargs["messages"]
        self.assertEqual(later[-2], chain()[0]["message"])
        self.assertEqual(json.loads(later[-1]["content"]), result["tool_history"][0]["result"])
        self.assertEqual(later[-1]["tool_name"], "query_sales")
        self.assertEqual(set(result["evidence_ids"]), {e for h in result["tool_history"] for e in h["result"]["evidence_ids"]})

    def test_batch_retry_still_multiple_explains_each_request(self):
        batch = call("query_sales", {})
        batch["message"]["tool_calls"] *= 2
        result, chat = self.run_fake([batch, batch], trace=True)
        self.assertEqual(result["stop_reason"], "multiple_calls_rejected")
        self.assertEqual(result["rejected_tool_batch"]["reason"], "multiple_calls_after_single_retry")
        self.assertEqual(len(result["rejected_tool_batch"]["calls"]), 2)
        self.assertIn("rejected_tool_call", result)
        self.assertEqual(result["tool_history"], [])
        self.assertEqual(result["evidence_ids"], [])
        self.assertEqual(chat.call_count, 2)

    def test_batch_retry_budget_insufficient(self):
        batch = call("query_sales", {})
        batch["message"]["tool_calls"] *= 2
        result, chat = self.run_fake([batch], trace=True, max_model_calls=1)
        self.assertEqual(result["stop_reason"], "model_limit")
        self.assertFalse(result["rejected_tool_batch"]["retry_attempted"])
        self.assertEqual(chat.call_count, 1)
        self.assertFalse(result["tool_history"])

    def test_batch_retry_model_exception_or_network_timeout(self):
        batch = call("query_sales", {})
        batch["message"]["tool_calls"] *= 2
        for failure in (RuntimeError("PRIVATE"), TimeoutError("PRIVATE")):
            result, chat = self.run_fake([batch, failure], trace=True)
            self.assertEqual(result["stop_reason"], "model_error")
            self.assertEqual(result["rejected_tool_batch"]["retry_result"], "model_error")
            self.assertNotIn("PRIVATE", str(result))
            self.assertFalse(result["tool_history"])
            self.assertEqual(chat.call_count, 2)

    def test_batch_retry_uses_same_total_deadline(self):
        release = Event()
        batch = call("query_sales", {})
        batch["message"]["tool_calls"] *= 2
        requests = []

        def fake(**kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                return batch
            release.wait(2)
            return call("query_sales", {})

        try:
            result = StoreDiagnosisAgent(chat=fake, total_seconds=.05).ask(QUESTION, trace=True)
        finally:
            release.set()
        self.assertEqual(result["stop_reason"], "time_limit")
        self.assertEqual(len(requests), 2)
        self.assertFalse(result["tool_history"])
        self.assertEqual(result["rejected_tool_batch"]["retry_result"], "time_limit")

    def test_batch_unknown_tool_is_never_executed(self):
        batch = call("query_sales", {})
        batch["message"]["tool_calls"].append(call("approve_purchase", {})["message"]["tool_calls"][0])
        result, _ = self.run_fake([batch, final(), final()], trace=True)
        self.assertFalse(result["tool_history"])
        self.assertFalse(result["evidence_ids"])
        item = result["rejected_tool_batch"]["calls"][1]
        self.assertFalse(item["registered"])
        self.assertEqual(item["reason"], "tool_not_registered")

    def test_batch_does_not_authorize_guessed_inventory_on_retry(self):
        inventory = call("query_inventory", {"product": "基础T恤", "color": "白色", "size": "M"})
        batch = call("query_sales", {})
        batch["message"]["tool_calls"].append(inventory["message"]["tool_calls"][0])
        result, _ = self.run_fake([batch, inventory], trace=True)
        self.assertEqual(result["stop_reason"], "tool_not_open")
        self.assertFalse(result["tool_history"])
        self.assertFalse(result["evidence_ids"])
        self.assertIn("rejected_tool_call", result)

    def test_malformed_batch_is_not_retried_as_legal_batch(self):
        batch = call("query_sales", {})
        batch["message"]["tool_calls"].append(None)
        result, chat = self.run_fake([batch], trace=True)
        self.assertEqual(result["stop_reason"], "invalid_tool_call")
        self.assertEqual(result["rejected_tool_batch"]["stage"], "native_tool_call_parse")
        self.assertFalse(result["rejected_tool_batch"]["structure_valid"])
        self.assertEqual(chat.call_count, 1)

    def test_later_batch_has_one_retry_and_model_text_cannot_change_facts(self):
        first = call("query_sales", {})
        first["message"]["tool_calls"] *= 2
        later = call("query_replenishment", {"product": "基础T恤"})
        later["message"]["tool_calls"] *= 2
        responses = [first, chain()[0], later, chain()[1], chain()[2], final("营业额99999999元，当前库存50件")]
        result, chat = self.run_fake(responses, trace=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(chat.call_count, 6)
        self.assertEqual(len(result["rejected_tool_batches"]), 2)
        self.assertTrue(all(b["retry_attempted"] for b in result["rejected_tool_batches"]))
        self.assertNotIn("99999999", result["final_text"])
        self.assertEqual(result["inventory_findings"][0]["quantity"], 0)

    def test_batch_diagnostics_hidden_without_trace(self):
        batch = call("query_sales", {})
        batch["message"]["tool_calls"] *= 2
        result, _ = self.run_fake([batch, batch])
        self.assertNotIn("rejected_tool_batch", result)
        self.assertNotIn("rejected_tool_batches", result)

    def test_dynamic_tool_lists_follow_verified_evidence(self):
        result, chat = self.run_fake(chain(), trace=True)
        lists = [[tool["function"]["name"] for tool in c.kwargs["tools"]] for c in chat.call_args_list]
        self.assertEqual(lists[0], ["query_sales"])
        self.assertEqual(set(lists[1]), {"query_sales", "query_replenishment"})
        self.assertEqual(set(lists[2]), {"query_sales", "query_replenishment", "query_inventory"})
        self.assertEqual([r["opened_tools"] for r in result["model_rounds"]], lists)
        first = result["model_rounds"][0]["tool_availability"]
        self.assertEqual(first["query_inventory"]["reason"], "no_supported_complete_variant")
        replen = result["model_rounds"][1]["tool_availability"]["query_replenishment"]["candidates"]
        self.assertEqual(replen[0]["parameter_sources"]["product"]["evidence_id"], "CAT-001")
        inventory = result["model_rounds"][2]["tool_availability"]["query_inventory"]["candidates"]
        self.assertEqual(inventory[0]["parameter_sources"]["size"]["evidence_id"], "REP-001")

    def test_user_full_variant_opens_inventory_immediately(self):
        result, chat = self.run_fake([call("query_inventory", {"product": "基础T恤", "color": "白色", "size": "M"}), final()],
            question="商品：基础T恤，颜色：白色，尺码：M", trace=True)
        self.assertIn("query_inventory", result["model_rounds"][0]["opened_tools"])
        self.assertEqual(result["inventory_findings"][0]["quantity"], 0)

    def test_user_product_opens_replenishment_immediately(self):
        result, _ = self.run_fake([call("query_replenishment", {"product": "基础T恤"}), final()],
            question="请查询基础T恤的补货记录。", trace=True)
        first = result["model_rounds"][0]
        self.assertIn("query_replenishment", first["opened_tools"])
        self.assertNotIn("query_inventory", first["opened_tools"])
        candidate = first["tool_availability"]["query_replenishment"]["candidates"][0]
        self.assertEqual(candidate["parameter_sources"]["product"]["source"], "user_explicit")

    def test_opened_tool_still_rejects_wrong_actual_variant(self):
        responses = chain()[:2] + [call("query_inventory", {"product": "基础T恤", "color": "黑色", "size": "M"})]
        result, _ = self.run_fake(responses, trace=True)
        self.assertIn("query_inventory", result["model_rounds"][-1]["opened_tools"])
        self.assertEqual(result["stop_reason"], "unsupported_arguments")
        self.assertEqual(result["inventory_findings"], [])

    def test_closed_tool_selection_is_visible_but_not_executed(self):
        result, _ = self.run_fake([call("query_inventory", {"product": "基础T恤", "color": "白色", "size": "M"})], trace=True)
        self.assertEqual(result["stop_reason"], "tool_not_open")
        self.assertEqual(result["tool_history"], [])
        self.assertEqual(result["rejected_tool_call"]["stage"], "tool_availability")
        self.assertEqual(result["model_rounds"][0]["model_selection"][0]["tool"], "query_inventory")

    def test_opened_replenishment_empty_parameters_rejected(self):
        result, _ = self.run_fake([call("query_replenishment", {"product": ""})],
            question="请查询基础T恤的补货记录。", trace=True)
        self.assertIn("query_replenishment", result["model_rounds"][0]["opened_tools"])
        self.assertEqual(result["stop_reason"], "invalid_arguments")
        self.assertEqual(result["tool_history"], [])

    def test_early_finish_remains_partial_after_one_unanswered_reminder(self):
        result, chat = self.run_fake([chain()[0], final("已经全部查清，库存都是0"), final()], trace=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["stop_reason"], "model_finished")
        self.assertEqual(chat.call_count, 3)
        self.assertEqual(result["inventory_findings"], [])
        self.assertIn("query_replenishment", result["model_rounds"][1]["opened_tools"])

    def test_round_details_not_exposed_by_default(self):
        result, _ = self.run_fake(chain())
        self.assertNotIn("model_rounds", result)
        self.assertNotIn("requested_domains", result)
        self.assertNotIn("continuation_attempted", result)

    def test_continuation_finishes_missing_inventory(self):
        result, chat = self.run_fake(chain()[:2] + [final("调查完毕"), chain()[2], final()], trace=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(chat.call_count, 5)
        self.assertEqual(result["requested_domains"], ["sales", "inventory", "replenishment"])
        self.assertEqual(result["completed_domains"], result["requested_domains"])
        self.assertEqual(result["missing_domains"], [])
        self.assertTrue(result["continuation_attempted"])
        self.assertEqual(result["continuation_result"], "completed_requested_domains")
        messages = chat.call_args_list[3].kwargs["messages"]
        self.assertEqual(messages[-2], final("调查完毕")["message"])
        self.assertEqual(messages[-1]["role"], "user")
        self.assertIn("query_inventory", messages[-1]["content"])
        self.assertNotIn("白色", messages[-1]["content"])
        self.assertEqual(len(result["tool_history"]), 3)
        self.assertEqual(result["inventory_findings"][0]["quantity"], 0)
        self.assertEqual(result["inventory_findings"][0]["snapshot_at"], data.SNAPSHOT_AT)
        self.assertEqual(set(result["evidence_ids"]), {e for h in result["tool_history"] for e in h["result"]["evidence_ids"]})

    def test_continuation_still_no_tool_is_partial(self):
        result, chat = self.run_fake(chain()[:2] + [final(), final("库存查完了")], trace=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["stop_reason"], "model_finished")
        self.assertEqual(result["missing_domains"], ["inventory"])
        self.assertEqual(result["continuation_result"], "still_incomplete")
        self.assertEqual(chat.call_count, 4)
        self.assertEqual(result["inventory_findings"], [])

    def test_sales_only_finishes_without_other_domains(self):
        question = "请先调用销售查询工具，查询两个期间的营业额、订单数和成交率。"
        result, chat = self.run_fake([chain()[0], final()], question=question, trace=True)
        self.assertEqual(result["requested_domains"], ["sales"])
        self.assertEqual(result["missing_domains"], [])
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["continuation_attempted"])
        self.assertEqual(chat.call_count, 2)

    def test_replenishment_only_does_not_force_inventory(self):
        result, chat = self.run_fake([chain()[1], final()], question="请查询基础T恤的补货记录。", trace=True)
        self.assertEqual(result["requested_domains"], ["replenishment"])
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["continuation_attempted"])
        self.assertEqual(chat.call_count, 2)

    def test_inventory_without_supported_variant_does_not_continue(self):
        result, chat = self.run_fake([final()], question="请调查库存。", trace=True)
        self.assertEqual(result["requested_domains"], ["inventory"])
        self.assertEqual(result["missing_domains"], ["inventory"])
        self.assertFalse(result["continuation_attempted"])
        self.assertEqual(result["continuation_reason"], "no_supported_unexecuted_candidate")
        self.assertEqual(chat.call_count, 1)
        self.assertFalse(result["tool_history"])

    def test_continuation_does_not_bypass_parameter_checks(self):
        for args, reason in (({"product": "基础T恤", "color": "白色", "size": ""}, "invalid_arguments"),
                             ({"product": "基础T恤", "color": "黑色", "size": "M"}, "unsupported_arguments")):
            result, _ = self.run_fake(chain()[:2] + [final(), call("query_inventory", args)], trace=True)
            self.assertTrue(result["continuation_attempted"])
            self.assertEqual(result["stop_reason"], reason)
            self.assertEqual(result["inventory_findings"], [])
            self.assertEqual(len(result["tool_history"]), 2)

    def test_continuation_respects_model_budget(self):
        result, chat = self.run_fake(chain()[:2] + [final()], trace=True, max_model_calls=3)
        self.assertEqual(result["stop_reason"], "model_limit")
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["continuation_attempted"])
        self.assertEqual(result["continuation_reason"], "model_budget_exhausted")
        self.assertEqual(chat.call_count, 3)

    def test_continuation_respects_tool_budget(self):
        result, chat = self.run_fake(chain()[:2] + [final()], trace=True, max_tool_calls=2)
        self.assertEqual(result["stop_reason"], "tool_limit")
        self.assertFalse(result["continuation_attempted"])
        self.assertEqual(result["continuation_reason"], "tool_budget_exhausted")
        self.assertEqual(chat.call_count, 3)

    def test_continuation_not_reset_after_a_successful_tool(self):
        result, chat = self.run_fake([chain()[0], final(), chain()[1], final()], trace=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["missing_domains"], ["inventory"])
        self.assertEqual(chat.call_count, 4)
        self.assertEqual(len(result["tool_history"]), 2)

    def test_continuation_and_batch_retry_share_same_model_budget(self):
        batch = deepcopy(chain()[2])
        batch["message"]["tool_calls"] *= 2
        result, chat = self.run_fake(chain()[:2] + [final(), batch, chain()[2], final()], trace=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(chat.call_count, 6)
        self.assertTrue(result["continuation_attempted"])
        self.assertTrue(result["rejected_tool_batch"]["retry_attempted"])
        self.assertEqual(len(result["tool_history"]), 3)

    def test_continuation_timeout_keeps_partial_facts(self):
        release = Event()
        responses = iter(chain()[:2] + [final()])
        calls = []

        def fake(**kwargs):
            calls.append(kwargs)
            if len(calls) <= 3:
                return next(responses)
            release.wait(2)
            return chain()[2]

        try:
            result = StoreDiagnosisAgent(chat=fake, total_seconds=.2).ask(QUESTION, trace=True)
        finally:
            release.set()
        self.assertEqual(result["stop_reason"], "time_limit")
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["continuation_attempted"])
        self.assertEqual(result["inventory_findings"], [])


if __name__ == "__main__":
    unittest.main()
