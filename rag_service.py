"""???????? RAG ???????????????????"""

from copy import deepcopy
from policy_citations import partial_welfare_original
from policy_evidence_guard import check_policy_evidence, scope_question, city_levels, meal_risks
from policy_citations import applicable_policies, render_trusted_answer, validate_and_render, CitationError, safe_original_fallback

RERANK_THRESHOLD = 0.5

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

def build_reimbursement_guard_answer(question, final_policies, documents):
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

class RagService:
    """collection?reranker?documents ???????chat ????????"""

    def __init__(self, collection, reranker, documents, chat=None, *, rerank_threshold=RERANK_THRESHOLD):
        if type(rerank_threshold) not in (int, float) or rerank_threshold not in (0.5, 0.45, 0.4):
            raise ValueError("实验阈值仅支持0.50、0.45、0.40")
        self.rerank_threshold = float(rerank_threshold)
        self.collection = collection
        self.reranker = reranker
        self.documents = deepcopy(documents)
        self.chat = chat

    def retrieve_policy(self, question, top_k=5):

        # 多城市不得被原单城市函数的优先匹配缩成一个城市。
        city_level = None if len(city_levels(question)) > 1 else get_city_level(question)

        # 如果用户明确提供了城市等级
        if city_level:

            result = self.collection.query(
                query_texts=[question],
                n_results=top_k,
                where={
                    "$or": [
                        {"city_level": city_level},
                        {"city_level": ""}
                    ]
                }
            )

        # 如果用户没有提供城市等级
        else:

            result = self.collection.query(
                query_texts=[question],
                n_results=top_k
            )

        if not result["documents"] or not result["documents"][0]:

            return []

        candidates = []

        for document, metadata, distance in zip(
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0]
        ):

            candidates.append({
                "content": document,
                "source": metadata["source"],
                "policy_id": metadata["policy_id"],
                "distance": distance
            })

        return candidates

    def answer_question(self, question):
        """返回各阶段快照；policies 是提供的依据，不代表模型引用已验证。

        保持 CLI 的检查顺序；数据库和重排序异常仍向调用方传播。
        """
        result = {
            "status": "", "answer": "", "policies": [], "retrieved": [],
            "reranked": [], "final_policies": [], "message": ""
        }

        def finish(status, answer, policies=None, message=""):
            result.update(status=status, answer=answer, message=message)
            result["policies"] = deepcopy(policies or [])
            return result

        original_question = question
        question, ambiguous_scope = scope_question(question)
        if ambiguous_scope:
            return finish("guarded", "无法可靠确定主题排除的作用范围，请分别说明需要查询和排除的主题。")

        candidates = self.retrieve_policy(question, top_k=5)
        result["retrieved"] = deepcopy(candidates)
        if not candidates:
            return finish("no_policy", "知识库中没有找到候选政策。")

        reranked = self.rerank_policy(question, candidates)
        result["reranked"] = deepcopy(reranked)
        final_policies = [p for p in reranked if p["rerank_score"] >= self.rerank_threshold]
        levels = city_levels(question)
        if len(levels) > 1:
            allowed = {"tier_1": "hotel_001", "tier_2": "hotel_002", "other": "hotel_003"}
            final_policies = [p for p in final_policies if not p["policy_id"].startswith("hotel_")
                              or p["policy_id"] in {allowed[level] for level in levels}]
        else:
            final_policies = filter_policy_by_condition(question, final_policies)
        if question != original_question:
            final_policies = applicable_policies(question, final_policies, self.documents, get_city_level(question))
        # 有限的独立主题补检索；每次重新评分，绝不直接提升原低分候选。
        subqueries = []
        has_meal = any(w in question for w in ("饭补", "饭钱", "餐饮", "吃饭", "伙食"))
        has_other = any(w in question for w in ("住宿", "酒店", "硬座", "高铁", "交通"))
        if has_meal and has_other and not any(p['policy_id'] == 'meal_001' for p in final_policies):
            subqueries.append("出差餐饮补贴标准是多少？")
        if "住宿" in question and any(w in question for w in ("剩余", "差额", "余款")) and len(levels) == 1:
            if not any(p['policy_id'].startswith('hotel_') for p in final_policies):
                city = {"tier_1": "一线城市", "tier_2": "二线城市", "other": "其他城市"}[levels[0]]
                subqueries.append(city + "住宿标准是多少？")
        for subquery in subqueries:
            extra = self.retrieve_policy(subquery, top_k=5)
            scored = self.rerank_policy(subquery, extra)
            eligible = [p for p in scored if p['rerank_score'] >= self.rerank_threshold]
            eligible = filter_policy_by_condition(question, eligible)
            eligible = applicable_policies(subquery, eligible, self.documents, get_city_level(subquery))
            trusted = {p['policy_id']: p for p in self.documents}
            eligible = [p for p in eligible if p['content'] == trusted.get(p['policy_id'], {}).get('content')
                        and p['source'] == trusted.get(p['policy_id'], {}).get('source')]
            result.setdefault('subquery_retrievals', []).append(deepcopy({
                'question': subquery, 'retrieved': extra, 'reranked': scored, 'accepted': eligible}))
            seen = {p['policy_id'] for p in final_policies}
            final_policies.extend(p for p in eligible if p['policy_id'] not in seen)
        result["final_policies"] = deepcopy(final_policies)

        condition_ok, message = (True, "") if len(levels) > 1 else check_required_conditions(question)
        if not condition_ok:
            return finish("needs_input", message, message=message)
        if not final_policies:
            return finish("no_policy", "本次检索筛选后无可用政策依据，无法确认；不代表知识库一定没有相关规定。")

        levels = city_levels(question)
        if len(levels) > 1 and any(w in question for w in ("住宿", "酒店", "宾馆", "房费")):
            evidence = [p for p in final_policies if any(
                d["policy_id"] == p["policy_id"] and d.get("category") == "hotel"
                and d.get("city_level") in levels for d in self.documents)]
            body = "\n".join(p["content"] for p in evidence)
            body += "\n本次仅展示已取得的适用政策原文；必要城市或其他子问题的证据可能不完整，无法确认完整答案。这不代表知识库不存在相关规定。"
            return finish("guarded", render_trusted_answer(body, evidence), evidence)

        # 餐饮金额与非出差条件必须在生成前保护，也不能被住宿金额分支吞掉。
        if any(meal_risks(question)):
            handled, answer, evidence = check_policy_evidence(question, final_policies, self.documents, get_city_level(question))
            if handled:
                return finish("guarded", answer, evidence)

        handled, answer = build_reimbursement_guard_answer(
            question, final_policies, self.documents
        )
        if handled:
            return finish("guarded", answer, final_policies)

        # 原住宿金额保护优先；新保护只处理有限的证据不足情形。
        handled, answer, evidence = check_policy_evidence(
            question, final_policies, self.documents, get_city_level(question)
        )
        if handled:
            return finish("guarded", answer, evidence)

        partial = partial_welfare_original(question, final_policies, self.documents)
        if partial is not None:
            return finish("guarded", partial[0], partial[1])

        relevant = applicable_policies(question, final_policies, self.documents, get_city_level(question))
        if question != original_question:
            return finish("guarded", render_trusted_answer(
                "\n".join(p["content"] for p in relevant)
                + "\n仅展示明确查询主题的政策原文；其他推断无法确认。", relevant), relevant)
        if "原文" in question:
            original = safe_original_fallback(question, final_policies, self.documents, get_city_level(question))
            if original is not None:
                result["diagnostics"] = {"answer_origin": "python_original", "semantic_verified": False}
                return finish("ok", original[0], original[1])
            return finish("guarded", "无法可靠定位唯一适用政策原文，请补充条件或拆分提问。")

        prompt = build_prompt(question, final_policies)
        try:
            chat = self.chat
            if chat is None:
                # 仅在真正生成回答时导入，模拟测试不需要安装 Ollama。
                from ollama import chat
            response = chat(
                model="qwen2.5:3b",
                messages=[{"role": "user", "content": prompt}]
            )
            answer = response["message"]["content"]
        except Exception:
            message = (
                "【模型调用失败】无法获取回答，请检查 Ollama 服务是否运行，"
                "以及 qwen2.5:3b 模型是否可用。\n"
                "你可以继续输入问题，或输入 quit 退出。"
            )
            return finish("error", "", final_policies, message)
        try:
            answer, verified = validate_and_render(answer, final_policies, relevant)
        except CitationError as exc:
            result["diagnostics"] = {"citation_reason_code": exc.reason_code, "model_answer_discarded": True, "semantic_verified": False}
            original = safe_original_fallback(question, final_policies, self.documents, get_city_level(question))
            if original is not None:
                result["diagnostics"]["answer_origin"] = "python_original_fallback"
                return finish("ok", original[0], original[1])
            return finish("guarded", "生成回答的引用或原文未通过校验，或引用相关性需要人工复核；无法安全展示本次生成结论。请核对政策原文后重新提问。")
        return finish("ok", answer, verified)

    def rerank_policy(self, question, candidates):

        candidates = deepcopy(candidates)

        if not candidates:

            return []

        pairs = []

        for candidate in candidates:

            pairs.append([
                question,
                candidate["content"]
            ])

        scores = self.reranker.predict(pairs)

        for candidate, score in zip(candidates, scores):

            candidate["rerank_score"] = float(score)

        candidates.sort(
            key=lambda x: x["rerank_score"],
            reverse=True
        )

        return candidates