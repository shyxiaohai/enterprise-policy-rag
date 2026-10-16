"""有限证据保护：住宿/餐饮/交通组合、未规定交通及未满一年年假。

不提供通用语义理解，不根据评测题号判断，不推断政策之外的规则。
"""
import re


def scope_question(question):
    """仅移除明确的主题排除指令；不改写业务否定条件。"""
    pattern = r"(?:不问|不查|无需查询|不用查询)\s*(?:住宿|酒店|餐饮(?:补贴)?|餐费|交通)(?:规定|政策)?(?=[，,。；;！!？?]|$)"
    scoped = re.sub(pattern, "", question)
    ambiguous = bool(re.search(r"不问|不查|无需查询|不用查询", scoped))
    return scoped, ambiguous


def city_levels(question):
    # 支持“一线和二线城市”等共享后缀；不推断真实城市所属档位。
    return [level for word, level in (("一线", "tier_1"), ("二线", "tier_2"), ("其他城市", "other"))
            if word in question]


def meal_risks(question):
    """共享餐饮风险分类，避免普通部分证据分支抢占住宿金额保护。"""
    has_meal = any(w in question for w in ("餐饮", "餐费", "伙食", "吃饭", "饭补", "饭钱"))
    meal_question = "；".join(part for part in re.split(r"[，,；;。？?]", question)
                             if any(w in part for w in ("餐饮", "餐费", "伙食", "吃饭", "饭补", "饭钱")))
    if not any(w in question for w in ("住宿", "酒店", "宾馆", "房费")):
        meal_question = question
    # 明确连接的交通子句不属于餐饮金额问题；不删除业务否定或金额词。
    meal_question = "；".join(part for part in re.split(r"[；，,]|(?:和|及)(?=(?:普通列车|高铁|火车|交通))", meal_question)
                             if (not any(w in part for w in ("普通列车", "高铁", "火车", "交通"))
                                 or any(w in part for w in ("餐饮", "餐费", "伙食", "吃饭", "饭补", "饭钱"))))
    meal_rule = has_meal and (
        any(w in meal_question for w in ("超过", "超出", "上限", "限额", "比例", "差额", "全额", "剩余", "实报", "实际", "合计", "总共", "报销", "能报", "可报", "报多少", "报几成"))
        or (any(w in meal_question for w in ("花", "支付", "发票")) and any(w in meal_question for w in ("报", "领")))
    )
    non_trip = has_meal or "午饭" in question
    non_trip = non_trip and any(w in question for w in ("没有出差", "没出差", "非出差", "不出差"))
    return meal_rule, non_trip


def check_policy_evidence(question, policies, documents, city_level):
    """返回 handled、受控答案、实际使用的政策。只引用最终证据。"""
    data = {p["policy_id"]: p for p in documents}
    hotel = next((p for p in policies if city_level
                  and data.get(p["policy_id"], {}).get("category") == "hotel"
                  and data[p["policy_id"]].get("city_level") == city_level), None)
    meal = next((p for p in policies if p["policy_id"] == "meal_001"), None)
    transport = next((p for p in policies if p["policy_id"] == "transport_001"), None)
    has_meal = any(w in question for w in ("餐饮", "餐费", "伙食", "吃饭", "饭补", "饭钱"))
    meal_rule, non_trip = meal_risks(question)
    hotel_unknown = "住宿" in question and any(w in question for w in ("剩余", "差额", "实际报"))
    topics = []
    if any(w in question for w in ("住宿", "酒店", "宾馆", "房费")):
        topics.append(("住宿", hotel))
    if has_meal or non_trip:
        topics.append(("餐饮", meal))
    if any(w in question for w in ("交通", "火车", "列车", "高铁", "座位", "商务座", "飞机", "头等舱", "经济舱", "商务舱", "出租车", "打车", "机票", "航空", "航班", "软卧", "硬卧", "一等座")):
        topics.append(("交通", transport))

    # 本版只声明当前七条政策未覆盖的明确对象，不将允许项视为排他列表。
    unsupported_transport = any(w in question for w in (
        "飞机", "机票", "航空", "航班", "头等舱", "经济舱", "商务舱", "出租车", "打车", "软卧", "硬卧", "一等座"
    ))
    under_one_year = "年假" in question and (
        any(w in question for w in ("未满1年", "未满一年", "不满1年", "不满一年", "不足1年", "不足一年", "半年"))
        or bool(re.search(r"(?:工作|入职|工龄)\s*(?:只有|仅有|仅)?\s*(?:[1-9]|1[01]|[一二三四五六七八九]|十[一]?)\s*个月", question))
    )
    leave_stacking = "年假" in question and any(w in question for w in ("叠加", "累计", "再加", "相加", "加上"))
    # 批准是必要条件，不等于充分资格，更不能推出金额或比例。
    # 普通“未经批准能否报商务座”仍走原问答；金额问题无论是否提到审批均保护。
    approval_text = question
    for negative in ("未经批准", "未批准", "没有批准", "未审批", "没审批", "未经审批"):
        approval_text = approval_text.replace(negative, "")
    approval_asserted = any(w in approval_text for w in (
        "批准", "审批", "同意", "获批", "批了", "批过", "审核通过"
    ))
    asks_payment = any(w in question for w in (
        "报销", "能报", "可报", "可以报", "报多少", "报几成", "报几折", "报几元"
    ))
    asks_payment_detail = any(w in question for w in (
        "全额", "全部", "全报", "全款", "比例", "百分", "%", "％", "几成", "几折", "多少", "金额", "限额", "上限"
    )) or bool(re.search(r"\d+(?:\.\d+)?\s*(?:元|块)", question))
    uncertain_business_seat = "商务座" in question and asks_payment and (
        approval_asserted or asks_payment_detail
    )
    partial = len(topics) >= 2 and any(policy is None for _, policy in topics)
    # 有限的政策说明查询直接展示原文，不要求模型改写含条件的条款。
    transport_explanation = any(topic == "交通" for topic, _ in topics) and any(
        word in question for word in ("规定", "政策", "哪些", "介绍", "说明", "标准")
    )
    if not (unsupported_transport or under_one_year or leave_stacking or partial or uncertain_business_seat or transport_explanation or meal_rule or non_trip or hotel_unknown):
        return False, "", []

    answers, references = [], []
    for topic, policy in topics:
        if topic == "住宿" and hotel_unknown:
            if policy is not None:
                answers.append("住宿已知政策原文：" + policy['content'])
                references.append(policy)
            answers.append("住宿：实际报销计算及差额领取规则未明确，无法确认实际可报金额，也不能确认差额会发放或不发放。")
        elif topic == "餐饮" and (meal_rule or non_trip):
            if policy is not None:
                answers.append("餐饮已知政策原文：" + policy["content"])
                references.append(policy)
            else:
                answers.append("餐饮：本次适用证据不足，无法确认补贴标准。")
            if non_trip:
                answers.append("该条款限定出差期间；现有政策不足以确认非出差餐饮待遇，不能认定一定有或一定没有补贴。")
            if meal_rule:
                answers.append("政策未明确实际餐饮支出的报销资格、超出部分能否报销、上限、比例或差额领取规则，无法确认；不能从补贴标准推断。")
        elif topic == "交通" and unsupported_transport:
            answers.append("交通：当前政策没有明确规定所问交通方式或舱位的报销规则，无法确认可以或不可以报销；不能从火车座位规定推断。")
        elif topic == "交通" and uncertain_business_seat:
            if policy is not None:
                answers.append("交通已知政策原文：" + policy["content"])
                references.append(policy)
                answers.append("商务座：政策明确未经批准不得报销；但没有明确批准后的充分报销资格、报销金额或比例。无法确认批准后一定可报、全额报销或具体金额；审批条件不能替代这些规则。")
            else:
                answers.append("商务座：本次筛选后没有适用交通依据，无法确认审批后的报销资格、金额或比例。")
        elif policy is None:
            answers.append(f"{topic}：本次筛选后的适用证据不足，无法确认；这不代表知识库一定没有相关规定。")
        else:
            # 仅展示标准/规定原文，不替用户计算实际金额或解释审批后待遇。
            answers.append(f"{topic}已知政策原文：{policy['content']}")
            references.append(policy)
    if under_one_year:
        answers.append("年假：现有政策未明确未满1年的年假待遇，无法确认天数，不能据此认定为0天。")
    if leave_stacking:
        for policy in policies:
            if policy['policy_id'] in ('leave_001', 'leave_002'):
                answers.append("年假已知政策原文：" + policy['content'])
                references.append(policy)
        answers.append("现有政策未规定两档年假叠加规则，无法确认累计天数，不能据此承诺15天。")
    if transport_explanation:
        answers.append("交通条款按原文展示，保留审批前提；不能扩大为商务座无条件禁止，也不能反推批准后一定可报销。")
    answers.append("以上仅处理已识别的主题并展示可用原文；其他条件、计算或子问题请拆分提问，不代表已全部回答。")
    evidence = "\n".join(
        f"来源文件：{p['source']}；政策编号：{p['policy_id']}；政策内容：{p['content']}"
        for p in references
    ) or "没有支持所问结论的可引用政策依据。"
    return True, "【回答】\n" + "\n".join(answers) + "\n\n【政策依据】\n" + evidence, references
