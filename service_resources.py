"""界面无关的资源初始化；只打开显式指定的既有独立副本。"""

from pathlib import Path
from threading import Lock

from main import validate_test_database
from policy_loader import load_policy_documents
from rag_service import RagService


class SharedService:
    """同一进程中的整次问答串行执行。"""

    def __init__(self, service):
        self.service = service
        self.lock = Lock()

    def answer_question(self, question):
        with self.lock:
            return self.service.answer_question(question)


def build_service(database_path, policy_bytes=None, *, validator=None, loader=None):
    # 可注入轻量依赖，网页继续使用原来的检查入口和缓存键。
    path = (validator or validate_test_database)(database_path)
    documents = (loader or load_policy_documents)()
    policy_file = Path(__file__).resolve().parent / "knowledge_base" / "company_policy.txt"
    if policy_bytes is not None and policy_file.read_bytes() != policy_bytes:
        raise ValueError("政策文件加载期间发生变化")

    import chromadb
    import ollama
    from sentence_transformers import CrossEncoder

    client = chromadb.PersistentClient(path=str(path))
    collection = client.get_collection(name="company_policy")
    reranker = CrossEncoder("BAAI/bge-reranker-base")
    return SharedService(RagService(collection, reranker, documents, chat=ollama.chat))
