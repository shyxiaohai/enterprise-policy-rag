import chromadb


# ==================================================
# 1. 初始化 ChromaDB
# ==================================================

chroma_client = chromadb.PersistentClient(
    path="./chroma_db"
)

collection = chroma_client.get_or_create_collection(
    name="company_policy"
)

print("ChromaDB 初始化成功！")


# ==================================================
# 2. 企业政策知识库
# ==================================================

documents = [
    {
        "content": "一线城市出差住宿报销标准为500元/天。",
        "source": "company_policy.txt",
        "policy_id": "hotel_001"
    },
    {
        "content": "二线城市出差住宿报销标准为400元/天。",
        "source": "company_policy.txt",
        "policy_id": "hotel_002"
    },
    {
        "content": "其他城市出差住宿报销标准为300元/天。",
        "source": "company_policy.txt",
        "policy_id": "hotel_003"
    },
    {
        "content": "出差可以报销高铁二等座和普通列车硬座，未经批准不得报销商务座。",
        "source": "company_policy.txt",
        "policy_id": "transport_001"
    },
    {
        "content": "出差期间每天餐饮补贴标准为100元。",
        "source": "company_policy.txt",
        "policy_id": "meal_001"
    },
    {
        "content": "工作满1年的员工，每年享有5天带薪年假。",
        "source": "company_policy.txt",
        "policy_id": "leave_001"
    },
    {
        "content": "工作满3年的员工，每年享有10天带薪年假。",
        "source": "company_policy.txt",
        "policy_id": "leave_002"
    }
]


# ==================================================
# 3. 初始化知识库
# ==================================================

existing = collection.get()

existing_ids = set(existing["ids"])

new_documents = []
new_ids = []
new_metadatas = []


for document in documents:

    doc_id = document["policy_id"]

    if doc_id not in existing_ids:

        new_documents.append(document["content"])

        new_ids.append(doc_id)

        new_metadatas.append({
            "source": document["source"],
            "policy_id": document["policy_id"]
        })


if new_documents:

    collection.add(
        documents=new_documents,
        ids=new_ids,
        metadatas=new_metadatas
    )

    print("新增政策知识：", len(new_documents))

else:

    print("政策知识已经存在，无需重复添加。")


# ==================================================
# 4. Retrieval：负责检索
# ==================================================

def retrieve_policy(question, top_k=2, threshold=0.8):

    result = collection.query(
        query_texts=[question],
        n_results=top_k
    )

    if not result["documents"] or not result["documents"][0]:

        return []


    policies = []


    for document, metadata, distance in zip(
        result["documents"][0],
        result["metadatas"][0],
        result["distances"][0]
    ):

        # ChromaDB distance 越小，代表越相似
        if distance <= threshold:

            policies.append({
                "content": document,
                "source": metadata["source"],
                "policy_id": metadata["policy_id"],
                "distance": distance
            })


    return policies


# ==================================================
# 5. Prompt：负责构造上下文
# ==================================================

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

4. 回答必须保留政策中的适用条件。
例如：一线城市、二线城市、其他城市等条件不能省略。

5. 如果问题涉及多个政策，可以综合多个政策进行回答。

6. 如果需要进行简单数学计算，可以根据政策中的数字进行计算，
但不能引入政策之外的数据。

7. 回答时必须说明关键政策依据。

8. 如果政策没有明确说明某个事项，不要根据常识进行推测。

9. 回答要简洁、准确、清晰。

10. 政策依据必须保留来源文件和政策编号。

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


# ==================================================
# 6. Generation：暂时模拟大模型
# ==================================================

def generate_answer(prompt):

    print("\n==============================")
    print("Generation")
    print("==============================")

    print("大模型收到的 Prompt：")

    print(prompt)

    print("\n这里以后接真正的大模型 API。")


# ==================================================
# 7. 主程序：连续问答
# ==================================================

while True:

    question = input("\n请输入你的问题：")


    # ---------- 退出 ----------

    if question.lower() == "quit":

        print("\n程序已退出。")

        break


    # ---------- Retrieval ----------

    policies = retrieve_policy(question)


    print("\n==============================")
    print("检索结果")
    print("==============================")


    for policy in policies:

        print("------------------------------")

        print("政策内容：", policy["content"])

        print("来源：", policy["source"])

        print("政策编号：", policy["policy_id"])

        print("距离：", policy["distance"])


    # ---------- 判断是否找到相关政策 ----------

    if not policies:

        print("\n根据当前企业政策，无法找到相关信息。")

        continue


    # ---------- Prompt ----------

    prompt = build_prompt(
        question,
        policies
    )


    # ---------- Generation ----------

    generate_answer(prompt)