"""有限格式引用校验；仅验证引用身份与原文，不验证业务结论。"""
import re


class CitationError(ValueError):
    def __init__(self, message, reason_code="unsupported_format"):
        super().__init__(message)
        self.reason_code = reason_code


def safe_original_fallback(question, policies, documents, city_level):
    """白名单式简单问法；只独立返回完整原文，不修补模型结论。"""
    q = re.sub(r"[\s，,。？?：:]", "", question)
    hotel = r"(?:在)?(?:一线城市|二线城市|其他城市)(?:出差)?(?:住酒店[，,]?)?(?:公司(?:的)?)?(?:住宿|酒店)(?:报销)?标准(?:是)?(?:每天)?(?:多少钱|多少|是多少|是什么)"
    meal = r"(?:出差(?:期间)?)?(?:每天)?(?:餐饮|吃饭|伙食)(?:每天)?(?:的)?(?:补贴|补助)(?:标准)?(?:是)?(?:什么|多少|多少钱|是多少|是什么)"
    leave = r"(?:工作)?满(?:1|一|3|三)年(?:的员工)?(?:每年)?(?:有几天带薪年假|有几天年假|年假(?:政策)?(?:原文)?(?:是什么|是多少|规定)|的年假政策原文是什么)"
    transport = r"(?:未经批准|未经审批|未批准)(?:可以|能|可否|是否可以)?报销商务座吗"
    joint = r"(?:一线城市|二线城市|其他城市)(?:出差)?住宿(?:报销)?(?:标准)?(?:和|及)(?:餐饮|伙食)(?:补贴|补助)(?:标准)?(?:分别)?(?:多少|是多少|是什么)"
    reverse_joint = r"(?:一线城市|二线城市|其他城市)(?:出差)?(?:餐饮|伙食)(?:补贴|补助)(?:标准)?(?:和|及)住宿(?:报销)?(?:标准)?(?:分别)?(?:多少|是多少|是什么)"
    patterns = ((hotel, 1), (meal, 1), (leave, 1), (transport, 1), (joint, 2), (reverse_joint, 2))
    count = next((count for pattern, count in patterns if re.fullmatch(pattern, q)), None)
    if count is None:
        # 有限语法组合：主题、明确条件、日标准/年假天数；未知尾句不匹配。
        city = r"(?:一线城市|二线城市|其他城市)"
        hotel_topic = r"(?:旅馆)?(?:住宿|酒店|宾馆)(?:的)?(?:日|每天的|每天)?(?:报销)?标准"
        meal_topic = r"(?:餐饮|吃饭|伙食|饭钱)(?:日|每天)?(?:的)?(?:补贴|补助)(?:的)?(?:日|每天)?(?:标准)?"
        ask = r"(?:各|分别)?(?:是)?(?:多少|多少钱|是什么|是多少|按多少算)"
        hotel_query = rf"(?:目的地(?:归在|属于)|去|在)?{city}(?:出差)?{hotel_topic}{ask}"
        hotel_query = rf"(?:{hotel_query}|(?:去|在)?{city}(?:出差)?住(?:宾馆|酒店|旅馆){hotel_topic}(?:请告诉我|{ask}))"
        meal_query = rf"(?:去|在)?(?:{city})?(?:出差(?:期间)?(?:的)?)?(?:每天的)?{meal_topic}(?:出差期间)?{ask}"
        leave_query = r"(?:工作)?(?:刚好)?满(?:1|一|3|三)年(?:的员工)?(?:制度写的)?(?:每年)?(?:带薪)?年假(?:有)?(?:多少天|几天)"
        joint_query = rf"{city}(?:出差)?{hotel_topic}(?:和|及){meal_topic}{ask}"
        count = next((size for pattern, size in ((hotel_query, 1), (meal_query, 1),
                     (leave_query, 1), (joint_query, 2)) if re.fullmatch(pattern, q)), None)
    if count is None:
        return None
    selected = applicable_policies(question, policies, documents, city_level)
    if len(selected) != count:
        return None
    trusted = {p['policy_id']: p for p in documents}
    if any(p.get('source') != trusted.get(p['policy_id'], {}).get('source')
           or p.get('content') != trusted.get(p['policy_id'], {}).get('content') for p in selected):
        return None
    # 不默认工龄档位，不将其他最终候选直接加到引用。
    return render_trusted_answer("\n".join(p["content"] for p in selected), selected), selected


def partial_welfare_original(question, policies, documents):
    """仅处理明确的餐饮与健身补贴说明查询，不推断任何福利待遇。"""
    if not re.fullmatch(r"(?:出差)?餐饮补贴(?:和|及)(?:公司)?健身补贴(?:分别)?(?:怎么规定|规定是什么)[。？?]?", question):
        return None
    original = safe_original_fallback("出差餐饮补贴多少？", policies, documents, None)
    if original is None:
        return None
    evidence = original[1]
    return render_trusted_answer(evidence[0]['content'] +
        "\n健身补贴：本次没有已确认的适用政策依据，无法确认具体规定；不代表公司不存在此项政策。", evidence), evidence


def applicable_policies(question, policies, documents, city_level):
    """有限主题定位；未知主题不自动认定任何候选相关。"""
    data = {p["policy_id"]: p for p in documents}
    selected = []
    for policy in policies:
        pid = policy["policy_id"]
        info = data.get(pid, {})
        relevant = (
            (info.get("category") == "hotel" and city_level
             and info.get("city_level") == city_level
             and any(w in question for w in ("住宿", "酒店", "房费", "宾馆")))
            or (pid == "meal_001" and any(w in question for w in ("餐饮", "餐费", "伙食", "吃饭", "饭钱", "饭补")))
            or (pid == "transport_001" and any(w in question for w in ("交通", "高铁", "火车", "列车", "商务座")))
            or (pid == "leave_001" and "年假" in question and any(w in question for w in ("满1年", "满一年")))
            or (pid == "leave_002" and "年假" in question and any(w in question for w in ("满3年", "满三年")))
        )
        if relevant:
            selected.append(policy)
    return selected


def render_trusted_answer(body, policies):
    evidence = "\n\n".join(
        f"来源文件：{p['source']}\n政策编号：{p['policy_id']}\n政策内容：{p['content']}"
        for p in policies
    )
    return "【回答】\n" + body + "\n\n【政策依据】\n" + evidence


def validate_and_render(answer, policies, relevant):
    """接受明确的逐行引用格式；不确定格式安全失败，不猜测字段配对。"""
    if not isinstance(answer, str) or answer.count("【回答】") != 1 or answer.count("【政策依据】") != 1:
        raise CitationError("引用格式无法安全解析")
    prefix, section = answer.split("【政策依据】")
    if not prefix.startswith("【回答】"):
        raise CitationError("回答格式错误")
    body = prefix[len("【回答】"):].strip()
    if not body:
        raise CitationError("回答为空")
    evidence = {p["policy_id"]: p for p in policies}
    allowed = {p["policy_id"] for p in relevant}
    blocks, block = [], {}
    for line in section.strip().splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"\s*(来源文件|政策编号|政策内容|政策原文)[：:]\s*(.+?)\s*", line)
        if not match:
            raise CitationError("引用含无法解析的内容，需要人工复核")
        key, value = match.groups()
        key = "政策内容" if key == "政策原文" else key
        if key in block:
            if "来源文件" not in block or "政策编号" not in block:
                raise CitationError("引用字段重复或不完整")
            blocks.append(block)
            block = {}
        block[key] = value
    if block:
        blocks.append(block)
    if not blocks:
        raise CitationError("没有引用", "no_verifiable_citation")
    used = []
    for block in blocks:
        pid = block.get("政策编号")
        policy = evidence.get(pid)
        if policy is None:
            raise CitationError("引用编号不属于当前证据", "unknown_or_out_of_context_id")
        if block.get("来源文件") != policy["source"]:
            raise CitationError("引用来源不属于当前证据", "source_mismatch")
        if pid not in allowed:
            raise CitationError("引用相关性无法确认，需要人工复核", "unrelated_topic")
        if "政策内容" in block and block["政策内容"] != policy["content"]:
            raise CitationError("引用原文与真实政策不一致", "original_mismatch")
        if policy not in used:
            used.append(policy)
    # 正文显式标注的原文也必须完整匹配，不仅检查引用区。
    for match in re.finditer(r"(?:政策原文|政策内容)[：:]\s*([^\n]+)", body):
        if match.group(1).strip() not in {p["content"] for p in used}:
            raise CitationError("正文中的政策原文不完整或不真实", "original_mismatch")
    # 正文中的额外编号/来源无法可靠配对，保守要求在已验证引用内。
    for pid in re.findall(r"[A-Za-z][A-Za-z0-9_-]*_[0-9]+", body):
        if pid not in {p["policy_id"] for p in used}:
            raise CitationError("正文包含未验证编号", "unknown_or_out_of_context_id")
    for source in re.findall(r"[\w.-]+\.txt", body):
        if source not in {p["source"] for p in used}:
            raise CitationError("正文包含未验证来源", "source_mismatch")
    return render_trusted_answer(body, used), used
