"""只供显式trace的有限拒绝诊断，不参与执行许可判断。"""

from collections.abc import Mapping
import re

FIELDS = ("product", "color", "size", "store_id", "baseline", "comparison")


def batch_rejection(calls, registered):
    """最多展示10个请求；结构是否合法不等于参数已获执行许可。"""
    def shape(call):
        return (isinstance(call, Mapping) and call.get("type", "function") == "function"
                and isinstance(call.get("function"), Mapping)
                and isinstance(call["function"].get("name"), str)
                and bool(call["function"]["name"])
                and isinstance(call["function"].get("arguments"), Mapping))

    items = []
    for index, call in enumerate(calls[:10]):
        valid = shape(call)
        function = call.get("function") if isinstance(call, Mapping) else None
        function = function if isinstance(function, Mapping) else {}
        name = function.get("name")
        known = isinstance(name, str) and name in registered
        detail = rejection(name, function.get("arguments"), "batch_selection",
                           "invalid_function_call_structure" if not valid else
                           ("not_executed_requires_single_selection" if known else "tool_not_registered"))
        items.append(dict(index=index, structure_valid=valid, registered=known,
                          tool=detail["tool"], arguments=detail["arguments"], reason=detail["reason"]))
    valid = all(shape(call) for call in calls)
    return dict(request_count=len(calls), calls=items, omitted_count=max(0, len(calls)-10),
                structure_valid=valid, stage="batch_selection" if valid else "native_tool_call_parse",
                reason="expected_single_tool_call" if valid else "malformed_tool_call_batch",
                executed_count=0, retry_attempted=False, retry_result=None)


def safe_value(value):
    """不回显嵌套对象、路径、常见凭据或无限长模型内容。"""
    if value is None or type(value) in (bool, int, float):
        return value
    if not isinstance(value, str):
        return "[非字符串参数]"
    if re.search(r"[A-Za-z]:[\\/]|\\\\|(?<!\w)/|sk-|bearer|password|secret|token|api.?key", value, re.I):
        return "[敏感值已隐藏]"
    if len(value) > 80:
        return "[超长值已隐藏]"
    return value


def rejection(name, arguments, stage, reason, *, field_checks=None, validation_errors=None):
    return {
        "tool": safe_value(name),
        "arguments": {key: safe_value(arguments[key]) for key in FIELDS if key in arguments}
                     if isinstance(arguments, Mapping) else "[参数非对象或不可用]",
        "stage": stage, "reason": reason,
        "field_checks": field_checks or {},
        "validation_errors": validation_errors or [],
    }


def validation_details(exc):
    """仅取校验位置与错误类型，不读取input、context、异常字符串。"""
    if not callable(getattr(exc, "errors", None)):
        return []
    return [{"field": error["loc"][0] if error["loc"] and error["loc"][0] in FIELDS else "[结构或额外字段]",
             "error_type": safe_value(error["type"])}
            for error in exc.errors(include_url=False, include_context=False, include_input=False)[:8]]


def source_details(name, args, user, history, store_id):
    """解释已拒绝请求；逐字段匹配不代表整组来源通过，决定仍由parameter_sources作出。"""
    scope = {"store_id": store_id}
    if name == "query_sales":
        scope.update(baseline="P1", comparison="P2")
    checks = {}
    for field, value in args.items():
        if field in scope:
            checks[field] = {"value": safe_value(value), "source": "application_scope",
                             "expected": scope[field], "matched": value == scope[field]}
            continue
        matches = []
        for index, entry in enumerate(history):
            result = entry.get("result")
            if entry.get("status") != "verified" or not result:
                continue
            rows = []
            if name == "query_replenishment" and entry["tool"] == "query_sales" and result["status"] == "ok":
                rows = result["hot_products"]
            if entry["tool"] == "query_replenishment" and result["status"] == "ok":
                rows = [dict(product=row["product"], evidence_id=row["evidence_id"], **item)
                        for row in result["records"] for item in row["items"]]
            if entry["tool"] == "query_inventory" and result.get("record"):
                rows = [result["record"]]
            matches.extend({"history_index": index, "evidence_id": safe_value(row["evidence_id"])}
                           for row in rows if row.get(field) == value)
        checks[field] = {"value": safe_value(value), "user_value": safe_value(user.get(field)),
                         "user_match": field in user and user[field] == value,
                         "user_conflict": field in user and user[field] != value,
                         "tool_evidence_matches": matches[:10]}
    if any(field in scope and args[field] != scope[field] for field in args):
        reason = "application_scope_mismatch"
    elif any(field in user and user[field] != value for field, value in args.items() if field not in scope):
        reason = "conflicts_with_explicit_user_conditions"
    elif not user and not any(h.get("status") == "verified" for h in history):
        reason = "user_conditions_not_recognized_and_no_prior_evidence"
    else:
        reason = "no_supported_complete_parameter_bundle"
    return reason, checks
