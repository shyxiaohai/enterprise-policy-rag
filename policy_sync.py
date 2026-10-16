"""显式政策同步入口；导入模块不会打开数据库或执行同步。"""

from pathlib import Path
import sys

from policy_loader import load_policy_documents


DATABASE_PATH = Path(__file__).resolve().parent / "chroma_db"
COLLECTION_NAME = "company_policy"


class PolicySyncError(RuntimeError):
    """processed 只统计已正常返回的写操作，不承诺失败操作未落盘。"""

    def __init__(self, processed, policy_id=None):
        self.processed = processed
        self.policy_id = policy_id
        detail = "读取数据库记录失败" if policy_id is None else "处理政策失败：" + policy_id
        super().__init__(f"{detail}；已成功处理 {processed} 条。同步已停止，可能存在部分更新，请核对数据库状态。")


def sync_policies(collection, documents):
    """按原逻辑新增或更新；异常立即停止，不删除任何旧政策。"""
    processed = 0
    added = 0
    updated = 0
    try:
        existing_ids = set(collection.get()["ids"])
    except Exception as exc:
        raise PolicySyncError(processed) from exc

    for document in documents:
        doc_id = document["policy_id"]
        try:
            metadata = {
                "source": document["source"],
                "policy_id": doc_id,
                "category": document.get("category", ""),
                "city_level": document.get("city_level", ""),
                "amount": document.get("amount", 0),
                "unit": document.get("unit", ""),
                "scope": document.get("scope", "")
            }
            arguments = {
                "ids": [doc_id], "documents": [document["content"]],
                "metadatas": [metadata]
            }
            if doc_id in existing_ids:
                collection.update(**arguments)
                updated += 1
            else:
                collection.add(**arguments)
                added += 1
            processed += 1
        except Exception as exc:
            raise PolicySyncError(processed, doc_id) from exc
    return {"processed": processed, "added": added, "updated": updated}


def main():
    """只在显式调用时同步；先解析成功，再导入和初始化 ChromaDB。"""
    try:
        documents = load_policy_documents()
    except Exception as exc:
        print(f"政策解析失败，同步未启动；已成功处理 0 条：{exc}", file=sys.stderr)
        return 1
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(DATABASE_PATH))
        collection = client.get_or_create_collection(name=COLLECTION_NAME)
        result = sync_policies(collection, documents)
    except PolicySyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"数据库初始化失败，同步已停止；已成功处理 0 条：{exc}", file=sys.stderr)
        return 1
    print(f"政策同步成功：处理 {result['processed']} 条，新增 {result['added']} 条，更新 {result['updated']} 条。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
