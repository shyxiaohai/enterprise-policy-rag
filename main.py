"""企业政策 CLI：使用已有数据库，不自动同步政策。"""

from pathlib import Path
import argparse
import os
import stat
import sys

from policy_loader import load_policy_documents
from rag_service import RagService

DATABASE_PATH = Path(__file__).resolve().parent / "chroma_db"
COLLECTION_NAME = "company_policy"


def print_result(result):
    """每个结果只输出一次正文或提示，不重复附加引用。"""
    status = result["status"]
    if status == "error":
        print(result["message"])
    elif status == "needs_input":
        print("\n【需要补充信息】")
        print(result["message"])
    else:
        print("\n【最终答案】")
        print(result["answer"])


def validate_test_database(value):
    """保守检查独立副本；仅检查文件信息，不打开数据库内容。"""
    if os.name not in ("nt", "posix"):
        raise ValueError("当前平台无法可靠检查链接，拒绝测试数据库")

    def inspect(path):
        info = path.lstat()
        if os.name == "nt":
            attributes = getattr(info, "st_file_attributes", None)
            if attributes is None:
                raise ValueError("无法检查 Windows 重解析点，拒绝测试数据库")
            if attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT，包含目录联接
                raise ValueError("测试路径含重解析点或目录联接")
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("测试路径含符号链接")
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError("测试路径含非普通文件")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError("测试文件存在硬链接或无法确认独立身份")

    # 在 resolve 前检查路径各级，避免解析后掩盖链接。
    supplied = Path(os.path.abspath(os.fspath(value)))
    for ancestor in reversed((supplied,) + tuple(supplied.parents)):
        inspect(ancestor)
    candidate = supplied.resolve(strict=True)
    formal = DATABASE_PATH.resolve()
    if candidate == formal or formal in candidate.parents:
        raise ValueError("测试数据库不能是正式库或其子目录")
    if DATABASE_PATH.exists() and candidate.samefile(DATABASE_PATH):
        raise ValueError("测试数据库与正式库身份相同")
    if not candidate.is_dir():
        raise ValueError("测试数据库必须是目录")
    database_file = candidate / "chroma.sqlite3"
    if not database_file.is_file():
        raise ValueError("测试副本缺少普通文件 chroma.sqlite3")
    formal_file = DATABASE_PATH / "chroma.sqlite3"
    if formal_file.exists() and database_file.samefile(formal_file):
        raise ValueError("测试 SQLite 文件与正式库身份相同")
    # 检查整个副本，避免索引文件通过链接指向其他位置。
    pending = [candidate]
    while pending:
        directory = pending.pop()
        for entry in directory.iterdir():
            inspect(entry)
            if entry.is_dir():
                pending.append(entry)
    return candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description="企业政策问答 CLI")
    parser.add_argument("--test-db-path", help="显式指定已存在的独立数据库测试副本")
    args = parser.parse_args(argv)
    try:
        documents = load_policy_documents()
    except Exception as exc:
        print(f"政策解析失败，问答未启动：{exc}", file=sys.stderr)
        return 1

    database_path = DATABASE_PATH
    if args.test_db_path is not None:
        try:
            database_path = validate_test_database(args.test_db_path)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"测试数据库安全校验失败，问答未启动：{exc}", file=sys.stderr)
            return 1
        print(f"【副本测试模式】数据库路径：{database_path}")

    if not database_path.is_dir() or not (database_path / "chroma.sqlite3").is_file():
        print("既有数据库不存在，问答未启动。请先显式执行政策同步。", file=sys.stderr)
        return 1

    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(database_path))
        collection = client.get_collection(name=COLLECTION_NAME)
    except Exception:
        print("无法打开既有数据库或 company_policy 集合，问答未启动。请检查数据库及政策同步状态。", file=sys.stderr)
        return 1

    try:
        import ollama
        from sentence_transformers import CrossEncoder
        print("正在加载 Reranker 模型...")
        reranker = CrossEncoder("BAAI/bge-reranker-base")
        service = RagService(collection, reranker, documents, chat=ollama.chat)
    except Exception:
        print("问答资源加载失败，请检查 Python 依赖及本地模型资源。", file=sys.stderr)
        return 1

    print("企业政策问答已启动，输入 quit 退出。当前使用：本地 Qwen2.5 3B")
    while True:
        try:
            question = input("\n请输入你的问题：")
        except (EOFError, KeyboardInterrupt):
            print("\n程序已退出。")
            return 0
        if question.lower() == "quit":
            print("\n程序已退出。")
            return 0
        try:
            result = service.answer_question(question)
        except Exception:
            print("检索或重排序等问答处理发生故障，未能完成本次请求。请检查资源状态；可继续提问或输入 quit 退出。")
            continue
        print_result(result)


if __name__ == "__main__":
    raise SystemExit(main())
