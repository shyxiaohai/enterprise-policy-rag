"""只根据用户条件与已验证结果计算候选工具，不执行任何查询。"""

from .fictional_data import STORE_ID


def tool_availability(user, history, source_checker):
    candidates = {"query_sales": [{"store_id": STORE_ID, "baseline": "P1", "comparison": "P2"}],
                  "query_replenishment": [], "query_inventory": []}

    def add(row):
        if row.get("product"):
            candidates["query_replenishment"].append({"store_id": STORE_ID, "product": row["product"]})
        if all(row.get(key) for key in ("product", "color", "size")):
            candidates["query_inventory"].append({"store_id": STORE_ID,
                **{key: row[key] for key in ("product", "color", "size")}})

    add(user)
    for entry in history:
        result = entry.get("result")
        if entry.get("status") != "verified" or not result:
            continue
        if entry["tool"] == "query_sales" and result["status"] == "ok":
            for row in result["hot_products"]:
                add(row)  # 只有商品，不会因此生成任何颜色/尺码。
        if entry["tool"] == "query_replenishment" and result["status"] == "ok":
            for record in result["records"]:
                for item in record["items"]:
                    add(dict(product=record["product"], color=item["color"], size=item["size"]))
        if entry["tool"] == "query_inventory" and result.get("record"):
            add(result["record"])
    decisions = {}
    for name, options in candidates.items():
        supported = []
        for args in options:
            sources = source_checker(name, args, user, history)
            if sources is not None and not any(item["arguments"] == args for item in supported):
                supported.append({"arguments": args, "parameter_sources": sources})
        decisions[name] = {
            "opened": bool(supported),
            "reason": ("application_scope" if name == "query_sales" else "supported_parameter_candidates")
                      if supported else "no_supported_product" if name == "query_replenishment" else "no_supported_complete_variant",
            "candidates": supported,
        }
    return decisions
