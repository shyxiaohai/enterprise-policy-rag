"""有限语法核对原始问题；不使用模型解释，不推断省略或否定范围。"""

import re

from .fictional_inventory import INVENTORY

PRODUCTS = sorted({key[0] for key in INVENTORY}, key=len, reverse=True)
COLORS = sorted({key[1] for key in INVENTORY}, key=len, reverse=True)
# 红色是明确颜色但没有库存记录；允许查询后正确返回variant_not_found。
COLORS.append("红色")


def identify_conditions(question):
    """仅接受完整匹配的单规格表达；失败返回None，绝不补默认条件。"""
    if not isinstance(question, str):
        return None
    if re.search(r'''不|别|改|或|还是|默认|假设|如果|忽略|比如|例如|示例|[“”"'`]''', question):
        return None
    text = re.sub(r"\s+", "", question)
    # 显式字段可查询未收录商品；字段顺序固定，名称必须明确写出。
    match = re.fullmatch(
        r"商品[：:]([^，,；;：:？?。]{1,80})[，,；;]颜色[：:]([^，,；;：:？?。]{1,80})"
        r"[，,；;]尺码[：:](XL|S|M|L)(?:码|号)?[。？?]?", text, re.I)
    if match:
        return dict(zip(("product", "color", "size"),
                        (match[1], match[2], match[3].upper())))
    product = "(?P<product>" + "|".join(re.escape(p) for p in PRODUCTS) + ")"
    color = "(?P<color>" + "|".join(re.escape(c) for c in COLORS) + ")"
    size = r"(?P<size>XL|S|M|L)(?:码|号)?"
    prefix = r"(?:请问|请帮我查一下|请帮我查|帮我查一下|帮我查|查询|查一下|查)?[，,]?"
    suffix = r"(?:还有几件|还有多少件|还有吗|有货吗|库存是多少|库存多少|库存|多少件|几件|有吗)?[。？?！!]?"
    for body in (product + r"[，,]?" + color + r"[，,]?" + size,
                 color + size + r"的?" + product):
        match = re.fullmatch(prefix + body + suffix, text, re.I)
        if match:
            values = match.groupdict()
            values["product"] = next(p for p in PRODUCTS if p.casefold() == values["product"].casefold())
            values["size"] = values["size"].upper()
            return values
    return None


def check_argument_support(question, arguments):
    identified = identify_conditions(question)
    if identified is None:
        return {"supported": False, "identified": None, "reason": "unclear_or_missing"}
    if identified != arguments:
        return {"supported": False, "identified": identified, "reason": "argument_mismatch"}
    return {"supported": True, "identified": identified, "reason": "matched"}
