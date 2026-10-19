"""有限领域识别与事实完成度；不调用模型或业务查询。"""

import json
import re

DOMAIN_TO_TOOL = {"sales": "query_sales", "inventory": "query_inventory", "replenishment": "query_replenishment"}
LABELS = {"sales": "销售", "inventory": "库存", "replenishment": "补货"}


def requested_domains(question):
    """只识别明确领域列表或单领域查询，不推断“全面”等模糊意图。"""
    if not isinstance(question, str) or re.search(r'''不|别|改|或|假设|如果|例如|比如|[“”"'`]''', question):
        return []
    text = re.sub(r"\s+", "", question)
    match = re.search(r"(?:调查|检查|核查|分析|查询)((?:销售|库存|补货)(?:(?:、|和|及|与|,|，)(?:销售|库存|补货))*)(?:环节|情况|问题)?[。？！?!]?$", text)
    if match:
        return [domain for domain, label in LABELS.items() if label in match[1]]
    present = [domain for domain, label in LABELS.items() if label in text]
    if len(present) == 1 and re.search(r"查询|查一下|调查|检查|核查|分析|查询工具", text):
        return present
    return []


def completed_domains(history):
    completed = set()
    for entry in history:
        result = entry.get("result")
        if entry.get("status") != "verified" or not result:
            continue
        if (entry["tool"] == "query_sales" and result.get("status") == "ok"
                and len(result.get("periods", [])) == 2 and result.get("changes") is not None):
            completed.add("sales")
        if (entry["tool"] == "query_inventory" and result.get("status") in ("in_stock", "out_of_stock")
                and result.get("record") is not None and result.get("quantity") is not None):
            completed.add("inventory")  # 0库存也是真实查询事实，不能用数量真值判断。
        if entry["tool"] == "query_replenishment" and result.get("status") == "ok" and result.get("records"):
            completed.add("replenishment")
    return [domain for domain in DOMAIN_TO_TOOL if domain in completed]


def progress_state(requested, history):
    completed = completed_domains(history)
    return {"requested_domains": list(requested), "completed_domains": completed,
            "missing_domains": [domain for domain in requested if domain not in completed]}


def remaining_tools(availability, missing, seen):
    """仅返回缺失领域中，已有来源且参数组合尚未执行的候选工具名。"""
    names = []
    for domain in missing:
        name = DOMAIN_TO_TOOL[domain]
        decision = availability[name]
        if decision["opened"] and any(
            (name, json.dumps(item["arguments"], sort_keys=True, ensure_ascii=False)) not in seen
            for item in decision["candidates"]
        ):
            names.append(name)
    return names
