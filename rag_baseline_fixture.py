"""????????????????????????????"""

def build_prompt(question, policies):

    context = ""

    for policy in policies:

        context += (
            f"来源文件：{policy['source']}\n"
            f"政策编号：{policy['policy_id']}\n"
            f"政策内容：{policy['content']}\n\n"
        )

    prompt = f"""
你是一个企业内部政策助手。

你的任务是严格根据【企业政策】回答【用户问题】。

请遵守以下规则：

1. 只能使用【企业政策】中提供的信息回答。
2. 不允许编造政策中不存在的信息。
3. 如果政策中没有足够的信息回答问题，请明确告诉用户：
“根据当前企业政策，无法找到相关信息。”
4. 如果用户问题缺少决定最终答案所必需的条件，不允许自行假设或选择条件，
必须明确指出缺少的条件，并请求用户补充。
5. 如果政策存在多个适用条件，必须根据用户明确提供的条件选择，
不能自行默认其中任何一个条件。
6. 回答必须保留政策中的适用条件。
7. 如果问题涉及多个政策，可以综合多个政策进行回答。
8. 如果需要进行简单数学计算，可以根据政策中的数字进行计算，
但不能引入政策之外的数据。
9. 回答时必须说明关键政策依据。
10. 如果政策没有明确说明某个事项，不要根据常识进行推测。
11. 回答要简洁、准确、清晰。
12. 政策依据必须保留来源文件和政策编号。

【报销金额判断约束】

1. “报销标准为X元/天”不自动等于固定补贴、实报实销或报销上限，
不得据此自行推断实际报销金额的计算规则。
2. 若用户询问实际花费对应的可报金额，而政策未明确计算规则，
必须说明已知报销标准及缺失的计算规则，并明确表示无法确认可报金额；
不得擅自认定可以报销标准金额、只能报销实际花费，或取两者较小值。
3. 上述简单数学计算仅在政策明确支持计算方法且所需条件齐全时才允许，
不得自行补充计算方法。
4. 对包含多个子问题的提问，必须分别判断政策依据是否充分：
有依据的部分正常回答，缺少依据的部分说明无法确认。
5. 禁止编造政策来源、政策编号及报销规则；来源和编号只能引用【企业政策】中提供的内容，
不得把仅说明报销标准的政策作为确定实际可报金额的充分依据。

【企业政策】

{context}

【用户问题】

{question}

请按照以下格式回答：

【回答】

直接回答用户的问题。

【政策依据】

列出支持这个回答的关键政策内容，
同时注明来源文件和政策编号。
"""

    return prompt

def get_city_level(question):

    if "一线城市" in question:
        return "tier_1"

    if "二线城市" in question:
        return "tier_2"

    if "其他城市" in question:
        return "other"

    return None

def check_required_conditions(question):

    # 如果问题涉及住宿，但没有明确城市
    if "住宿" in question:

        if (
            "一线城市" not in question
            and "二线城市" not in question
            and "其他城市" not in question
        ):

            return False, "请提供出差城市，以确定住宿报销标准。"

    return True, ""

def filter_policy_by_condition(question, policies):

    # 用户明确说一线城市
    if "一线城市" in question:

        policies = [
            policy
            for policy in policies
            if policy["policy_id"] != "hotel_002"
            and policy["policy_id"] != "hotel_003"
        ]

    # 用户明确说二线城市
    elif "二线城市" in question:

        policies = [
            policy
            for policy in policies
            if policy["policy_id"] != "hotel_001"
            and policy["policy_id"] != "hotel_003"
        ]

    # 用户明确说其他城市
    elif "其他城市" in question:

        policies = [
            policy
            for policy in policies
            if policy["policy_id"] != "hotel_001"
            and policy["policy_id"] != "hotel_002"
        ]

    return policies

def build_reimbursement_guard_answer(question, final_policies):
    """识别基础住宿金额判断，只用已选中的政策生成受控回答。"""
    import re

    # 先按餐饮主题分开，避免把餐饮实际花费误当成住宿花费。
    hotel_part = re.split(r"餐饮|餐费|伙食", question, maxsplit=1)[0]
    has_hotel = any(word in hotel_part for word in ("住宿", "酒店", "宾馆", "房费"))
    has_expense = any(word in hotel_part for word in (
        "实际", "花费", "花了", "支付", "发票", "住了"
    )) or bool(re.search(r"花\s*\d", hotel_part))
    asks_amount = any(word in hotel_part for word in (
        "报销", "能报", "可报", "可以报", "能领", "领取", "差额", "余款"
    ))
    asks_rule = any(word in hotel_part for word in (
        "全额", "最高", "上限", "限额", "不足标准", "没花满", "差额", "余款"
    ))
    if not has_hotel or not ((has_expense and asks_amount) or asks_rule):
        return False, ""

    city_level = get_city_level(question)
    policy_data = {document["policy_id"]: document for document in documents}
    hotel_policy = None
    meal_policy = None
    for policy in final_policies:
        data = policy_data.get(policy["policy_id"], {})
        if (city_level and data.get("category") == "hotel"
                and data.get("city_level") == city_level):
            hotel_policy = policy
        if policy["policy_id"] == "meal_001":
            meal_policy = policy

    answers = []
    references = []
    if hotel_policy:
        answers.append(
            "住宿：" + hotel_policy["content"]
            + "当前政策仅规定住宿标准，未明确实际报销金额的计算规则，"
            "无法确认实际可报金额、是否全额报销、最高限额或差额能否领取。"
        )
        references.append(hotel_policy)
    else:
        answers.append("住宿：当前选中的政策中没有对应城市等级的住宿依据，无法确认。")

    # 仅处理明确的餐饮标准查询，不把实际金额或累计计算当作标准查询。
    meal_match = re.search(r"餐饮|餐费|伙食", question)
    if meal_match:
        meal_part = question[meal_match.start():]
        asks_meal_standard = any(word in meal_part for word in ("标准", "补贴", "每天"))
        asks_meal_calculation = any(word in meal_part for word in (
            "实际", "花", "全额", "最高", "上限", "差额", "合计", "总共", "一共", "共计"
        )) or bool(re.search(r"\d+\s*[天日元块]", meal_part))
        if asks_meal_standard and not asks_meal_calculation:
            if meal_policy:
                answers.append("餐饮：" + meal_policy["content"])
                references.append(meal_policy)
            else:
                answers.append("餐饮：当前检索依据不足，无法确认餐饮补贴标准。")
        else:
            answers.append("餐饮：该子问题未能可靠识别为标准查询，请单独提问。")

    answers.append("以上仅处理已识别的住宿金额判断及餐饮标准；如有其他子问题，请拆分提问。")
    evidence = [
        f"来源文件：{policy['source']}；政策编号：{policy['policy_id']}；政策内容：{policy['content']}"
        for policy in references
    ]
    return True, "【回答】\n" + "\n".join(answers) + "\n\n【政策依据】\n" + (
        "\n".join(evidence) if evidence else "当前没有可引用的相关政策依据。"
    )