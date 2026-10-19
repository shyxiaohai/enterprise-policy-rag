"""标准库诊断字段测试，不加载Pydantic或模型。"""

import unittest

from .diagnostics import batch_rejection, rejection, safe_value, source_details


class DiagnosticTests(unittest.TestCase):
    def test_only_necessary_fields(self):
        result = rejection("query_replenishment", {"product": "基础T恤", "password": "PRIVATE"}, "parameter_sources", "test")
        self.assertEqual(result["arguments"], {"product": "基础T恤"})
        self.assertNotIn("PRIVATE", str(result))

    def test_sensitive_values_hidden(self):
        for value in (r"C:\private\secret.txt", "/private/secret.txt", "Bearer PRIVATE", "sk-PRIVATE", "x"*81):
            self.assertNotEqual(safe_value(value), value)

    def test_no_recognized_user_or_prior_evidence(self):
        reason, fields = source_details("query_replenishment", {"product": "基础T恤", "store_id": "SZ-DEMO-001"}, {}, [], "SZ-DEMO-001")
        self.assertEqual(reason, "user_conditions_not_recognized_and_no_prior_evidence")
        self.assertFalse(fields["product"]["user_match"])
        self.assertTrue(fields["store_id"]["matched"])

    def test_user_conflict(self):
        reason, fields = source_details("query_replenishment", {"product": "休闲衬衫", "store_id": "SZ-DEMO-001"}, {"product": "基础T恤"}, [], "SZ-DEMO-001")
        self.assertEqual(reason, "conflicts_with_explicit_user_conditions")
        self.assertTrue(fields["product"]["user_conflict"])

    def test_scope_mismatch(self):
        reason, fields = source_details("query_sales", {"store_id": "OTHER", "baseline": "P1", "comparison": "P2"}, {}, [], "SZ-DEMO-001")
        self.assertEqual(reason, "application_scope_mismatch")
        self.assertFalse(fields["store_id"]["matched"])

    def test_verified_evidence_match_explained_without_full_record(self):
        history = [{"tool": "query_sales", "status": "verified", "result": {
            "status": "ok", "hot_products": [{"product": "基础T恤", "evidence_id": "CAT-001", "private": "NOT_PRINTED"}]}}]
        reason, fields = source_details("query_replenishment", {"product": "基础T恤", "store_id": "SZ-DEMO-001"}, {}, history, "SZ-DEMO-001")
        self.assertEqual(fields["product"]["tool_evidence_matches"], [{"history_index": 0, "evidence_id": "CAT-001"}])
        self.assertNotIn("NOT_PRINTED", str(fields))

    def test_batch_names_and_whitelist_parameters(self):
        calls = [{"function": {"name": "query_sales", "arguments": {}}},
                 {"function": {"name": "query_inventory", "arguments": {
                     "product": "基础T恤", "color": "白色", "size": "M", "password": "PRIVATE"}}}]
        result = batch_rejection(calls, {"query_sales", "query_inventory"})
        self.assertEqual(result["request_count"], 2)
        self.assertTrue(result["structure_valid"])
        self.assertEqual(result["calls"][1]["arguments"], {"product": "基础T恤", "color": "白色", "size": "M"})
        self.assertNotIn("PRIVATE", str(result))

    def test_batch_structure_error_and_path_redaction(self):
        calls = [None, {"function": {"name": "query_replenishment", "arguments": {"product": r"C:\private\secret.txt"}}}]
        result = batch_rejection(calls, {"query_replenishment"})
        self.assertFalse(result["structure_valid"])
        self.assertEqual(result["stage"], "native_tool_call_parse")
        self.assertNotIn("private", str(result))

    def test_large_batch_diagnostic_is_bounded(self):
        result = batch_rejection([{"function": {"name": "unknown", "arguments": {}}}]*12, set())
        self.assertEqual(result["request_count"], 12)
        self.assertEqual(len(result["calls"]), 10)
        self.assertEqual(result["omitted_count"], 2)
        self.assertFalse(result["calls"][0]["registered"])


if __name__ == "__main__":
    unittest.main()
