"""Streamlit 企业政策问答：第一版仅允许显式数据库副本。"""

import os
import re
from threading import Lock

import streamlit as st

from main import validate_test_database
from policy_loader import load_policy_documents
from rag_service import RagService


TEST_DB_ENV = "POLICY_APP_TEST_DB_PATH"
APP_STYLES = """<style>
/* Native light theme also controls canvas-rendered debug tables. */
:root {color-scheme: light;}
body {background: #f6f8fc; color: #1e293b;}
main {color: #1e293b;}
main h1, main h2, main h3, main h4 {
    color: #183153 !important; letter-spacing: -.02em;
}
main label, main p, main small, main pre, main summary {
    color: #334155;
}
main form {background: #ffffff; border: 1px solid #cbd5e1;
    border-radius: 16px; padding: 1.25rem;}
main textarea, main input {
    background: #ffffff !important; color: #1e293b !important;
    caret-color: #1d4ed8; border-radius: 8px;
}
main textarea::placeholder, main input::placeholder {color: #475569; opacity: 1;}
main textarea:focus, main input:focus {
    outline: 2px solid #1d4ed8; outline-offset: 2px;
}
main button {border-radius: 8px;}
main button p {color: inherit;}
main button:hover {outline: 2px solid #1d4ed8; outline-offset: 1px;}
main button:focus-visible, main summary:focus-visible {
    outline: 3px solid #1d4ed8; outline-offset: 3px;
}
main details {background: #ffffff; border: 1px solid #cbd5e1; border-radius: 12px;}
main [role="alert"] {color: #1e293b; border: 1px solid #94a3b8; border-radius: 10px;}
main [role="alert"] p {color: #1e293b;}
</style>"""
EXAMPLES = (
    "一线城市住宿和餐饮补贴标准分别多少？",
    "未经批准可以报销商务座吗？",
    "工作满1年每年有几天带薪年假？",
    "一线城市住宿花650元能报多少？",
)


def display_text(value):
    """页面不输出本地绝对路径；不改动服务结果或真实评分。"""
    text = str(value or "")
    text = re.sub(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s\n，；。]*", "[本地路径已隐藏]", text)
    return re.sub(r"(?<![\w:/])/(?!/)[^\s\n，；。]+", "[本地路径已隐藏]", text)


class SharedService:
    """整次问答串行执行，保护共享的检索、重排序和模型资源。"""

    def __init__(self, service):
        self.service = service
        self.lock = Lock()

    def answer_question(self, question):
        with self.lock:
            return self.service.answer_question(question)


@st.cache_resource(show_spinner="正在加载问答资源…")
def get_resources(database_path, policy_bytes):
    # 缓存键包含政策内容，避免文件更新后沿用旧的政策标签。
    path = validate_test_database(database_path)
    documents = load_policy_documents()
    from pathlib import Path
    policy_file = Path(__file__).resolve().parent / "knowledge_base" / "company_policy.txt"
    if policy_file.read_bytes() != policy_bytes:
        raise ValueError("政策文件加载期间发生变化")

    import chromadb
    import ollama
    from sentence_transformers import CrossEncoder

    client = chromadb.PersistentClient(path=str(path))
    collection = client.get_collection(name="company_policy")
    reranker = CrossEncoder("BAAI/bge-reranker-base")
    return SharedService(RagService(collection, reranker, documents, chat=ollama.chat))


def submit_question(service, question):
    """只负责调用服务和转换未捕获异常，不实现任何 RAG 规则。"""
    try:
        return service.answer_question(question)
    except Exception:
        return {
            "status": "error", "answer": "", "message": "问答处理失败，请稍后重试或联系管理员检查检索和模型资源。",
            "policies": [], "retrieved": [], "reranked": [], "final_policies": []
        }


def render_result(result):
    st.subheader("回答与说明")
    status = result["status"]
    labels = {"ok": "已回答", "guarded": "安全保护 · 请留意适用范围", "needs_input": "需要补充信息",
              "no_policy": "本次证据不足", "error": "服务暂不可用"}
    st.caption("问答状态：" + labels.get(status, "未知状态"))
    if status == "error":
        # 不直接展示任意异常文本，也不沿用 CLI 的 quit 提示。
        st.error("暂时无法完成问答，请检查检索资源或 Ollama 服务后重新提交。")
    elif status == "needs_input":
        st.warning(display_text(result["message"] or result["answer"]))
    elif status == "no_policy":
        st.info(display_text(result["answer"]))
    elif status in ("ok", "guarded"):
        origin = result.get("diagnostics", {}).get("answer_origin", "")
        st.caption("政策原文回答" if origin.startswith("python_original") else
                   ("业务保护回答" if status == "guarded" else "AI回答 · 服务已完成引用校验"))
        st.caption("引用身份与原文校验不等于业务语义全部正确，请结合适用条件核对。")
        # 只展示服务的安全输出，不读取或展示诊断中的原始模型正文。
        st.write(display_text(result["answer"]))
    else:
        st.error("收到无法识别的回答状态，请重新提交。")

    st.divider()
    st.subheader("系统政策原文 · 本次提供的依据")
    st.caption("最终回答引用 · 仅展示服务实际返回的 policies；调试候选不等于最终引用。请核对完整原文及适用条件。")
    policies = result.get("policies", [])
    if not policies:
        st.caption("本次没有可展示的政策依据。")
    for policy in policies:
        with st.container(border=True):
            st.caption("政策引用 · 完整原文")
            st.text(display_text(f"原文件：{policy['source']}  ·  政策编号：{policy['policy_id']}"))
            st.write(display_text(policy["content"]))

    with st.expander("检索详情", expanded=False):
        st.caption("Chroma 距离通常越小越接近；Rerank 分数越高越相关，两者都不是答案正确率。")
        for key, title, explanation in (
            ("retrieved", "1 · ChromaDB 原始召回", "保持向量检索返回顺序，尚未经过重排序。"),
            ("reranked", "2 · Rerank 重排序", "按重排序分数从高到低排列，尚未完成阈值及城市条件筛选。"),
            ("final_policies", "3 · 筛选后的可信证据候选", "完成阈值和条件筛选，可能含独立子主题补检索结果；并非最终回答引用。")
        ):
            records = result.get(key, [])
            st.write(f"{title}（{len(records)} 条）")
            st.caption(explanation)
            rows = [
                {"顺序": index, "编号": display_text(policy["policy_id"]), "来源": display_text(policy["source"]), "政策正文": display_text(policy["content"]),
                 "Chroma 距离": policy.get("distance"), "Rerank 分数": policy.get("rerank_score")}
                for index, policy in enumerate(records, start=1)
            ]
            if rows:
                st.dataframe(rows, hide_index=True, width="stretch", row_height=72,
                             height=min(480, 40 + 72 * len(rows)))
            else:
                st.caption("无记录")
        for stage in result.get("subquery_retrievals", []):
            st.caption("独立子主题补检索：" + display_text(stage.get("question", "")))
            st.caption(f"召回 {len(stage.get('retrieved', []))} 条 · 重排序 {len(stage.get('reranked', []))} 条 · 接纳 {len(stage.get('accepted', []))} 条")


def main():
    st.set_page_config(page_title="企业AI智能知识库", page_icon="📘", layout="wide")
    st.markdown(APP_STYLES, unsafe_allow_html=True)
    st.title("企业AI智能知识库")
    st.caption("企业政策 · 有据可查 · 安全问答　|　演示版 v1.0")
    st.info("请输入具体问题，包含必要的城市等级或工作年限。政策未明确的事项将提示无法确认。")
    st.caption("已知限制：普通列车硬座的部分问法仍可能触发引用校验拒答（H09），本版保留保守处理。")

    raw_path = os.environ.get(TEST_DB_ENV, "").strip()
    if not raw_path:
        st.error("尚未配置测试数据库副本。请设置 POLICY_APP_TEST_DB_PATH 后重新启动网页。")
        return
    try:
        # 每次重执行仍先校验路径；不能因缓存命中绕过副本校验。
        path = validate_test_database(raw_path)
        from pathlib import Path
        policy_file = Path(__file__).resolve().parent / "knowledge_base" / "company_policy.txt"
        policy_bytes = policy_file.read_bytes()
        resource_key = (str(path), policy_bytes)
        if st.session_state.get("resource_key") != resource_key:
            st.session_state.pop("latest_result", None)
            st.session_state.pop("latest_question", None)
        service = get_resources(str(path), policy_bytes)
        st.session_state["resource_key"] = resource_key
    except Exception:
        st.error("初始化失败：请检查测试副本、政策文件及本地模型资源。页面未回退到正式数据库。")
        return

    with st.expander("示例问题 · 点击填入后提交", expanded=False):
        for index, example in enumerate(EXAMPLES):
            if st.button(example, key=f"example_{index}"):
                st.session_state["question_input"] = example
    with st.form("policy_question_form"):
        question = st.text_area("请输入政策问题", key="question_input", placeholder="例如：一线城市出差住宿和餐饮补贴分别是多少？", height=110)
        submitted = st.form_submit_button("查询政策", type="primary")
    if submitted:
        if not question.strip():
            st.warning("请先输入问题。")
        else:
            with st.spinner("正在检索政策并生成回答…"):
                result = submit_question(service, question.strip())
            st.session_state["latest_result"] = result
            st.session_state["latest_question"] = question.strip()

    if "latest_result" in st.session_state:
        st.caption("最近提交的问题")
        st.write(display_text(st.session_state["latest_question"]))
        render_result(st.session_state["latest_result"])


if __name__ == "__main__":
    main()
