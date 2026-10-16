"""政策差异检测：仅调用已有 collection 的 get，不创建数据库客户端。"""

import argparse
import json
from pathlib import Path

from policy_loader import load_policy_documents


SOURCE = "company_policy.txt"
COLLECTION_NAME = "company_policy"


def check_policy_sync(collection, policy_path=None):
    """先验证文件，再读取指定来源；异常向上传递，不生成不完整报告。"""
    policies = load_policy_documents(policy_path)
    if any(p["source"] != SOURCE for p in policies):
        raise ValueError("政策文件名必须为 company_policy.txt")
    if collection.name != COLLECTION_NAME:
        raise ValueError("必须使用 company_policy collection")
    try:
        result = collection.get(
            where={"source": SOURCE}, include=["documents", "metadatas"]
        )
    except Exception as exc:
        raise RuntimeError("数据库读取失败，差异检测终止，未生成报告") from exc
    return compare_policy_records(policies, result)


def compare_policy_records(policies, result):
    """existing 表示完全一致；changed 可与 needs_review 重叠。

    旧编号仅供核对，不代表可以删除。身份不明时暂停新增/缺失判断。
    """
    expected = {p["policy_id"]: p for p in policies}
    if len(expected) != len(policies):
        raise ValueError("文件政策编号重复")
    ids, texts, metadatas = (result.get(key) for key in ("ids", "documents", "metadatas"))
    if not all(isinstance(value, list) for value in (ids, texts, metadatas)):
        raise ValueError("数据库结果缺少 ids/documents/metadatas 列表")
    if not len(ids) == len(texts) == len(metadatas) or len(set(ids)) != len(ids):
        raise ValueError("数据库结果长度不一致或记录 ID 重复")
    report = {
        "source": SOURCE, "collection": COLLECTION_NAME,
        "new": [], "changed": [], "existing": [], "old_ids": [],
        "needs_review": [], "missing_in_database": []
    }
    grouped = {}
    observed_ids = set()
    identity_unknown = False
    for record_id, text, metadata in zip(ids, texts, metadatas):
        # 不接受混入其他来源的结果，避免报告越界。
        if not isinstance(metadata, dict) or not metadata.get("source"):
            raise ValueError("数据库结果缺失 source，检测终止，未生成报告")
        if metadata.get("source") != SOURCE:
            raise ValueError("数据库结果混入其他来源，检测终止，未生成报告")
        policy_id = metadata.get("policy_id")
        if not isinstance(policy_id, str) or not policy_id.strip():
            identity_unknown = True
            report["needs_review"].append({"record_id": record_id, "reason": "政策编号缺失/格式错误；暂停新增及缺失判断"})
            continue
        observed_ids.add(policy_id)
        if record_id != policy_id:
            report["needs_review"].append({"policy_id": policy_id, "record_id": record_id, "reason": "记录 ID 与政策编号不一致"})
        if not isinstance(text, str) or not text.strip():
            report["needs_review"].append({"policy_id": policy_id, "record_id": record_id, "reason": "正文缺失/格式错误"})
        grouped.setdefault(policy_id, []).append((record_id, text, metadata))
    for policy_id, records in grouped.items():
        if len(records) != 1:
            report["needs_review"].append({"policy_id": policy_id, "reason": "数据库政策编号重复", "record_ids": [r[0] for r in records]})
            continue
        record_id, text, metadata = records[0]
        if policy_id not in expected:
            report["old_ids"].append({"policy_id": policy_id, "record_id": record_id, "reason": "文件中不存在，仅供核对"})
            continue
        target = expected[policy_id]
        # 与 main.py 的入库默认值保持一致，元数据差异单独报告。
        defaults = {"category": "", "city_level": "", "amount": 0, "unit": "", "scope": ""}
        metadata_diff = {
            key: {"database": metadata.get(key), "file": target.get(key, default),
                  "database_type": type(metadata.get(key)).__name__,
                  "file_type": type(target.get(key, default)).__name__}
            for key, default in defaults.items()
            if (type(metadata.get(key)) is not type(target.get(key, default))
                or metadata.get(key) != target.get(key, default))
        }
        if metadata_diff:
            report["needs_review"].append({"policy_id": policy_id, "reason": "元数据差异", "fields": metadata_diff})
        if not isinstance(text, str) or not text.strip():
            continue
        if text != target["content"]:
            report["changed"].append({"policy_id": policy_id, "record_id": record_id, "database_content": text, "file_content": target["content"]})
        elif record_id == policy_id and not metadata_diff:
            report["existing"].append(policy_id)
    # “新增”表示文件有、该来源数据库没有；不是写入指令。
    missing = [] if identity_unknown else sorted(set(expected) - observed_ids)
    report["new"] = missing
    report["missing_in_database"] = missing.copy()
    report["existing"].sort()
    return report


class SnapshotCollection:
    """离线 get 快照适配器；不打开任何 ChromaDB 文件。"""
    name = COLLECTION_NAME

    def __init__(self, result):
        self.result = result

    def get(self, *, where, include):
        return self.result


def main():
    parser = argparse.ArgumentParser(description="只读比较政策文件与已导出的同来源 ChromaDB get JSON 快照")
    parser.add_argument("--snapshot", required=True, help="包含 ids、documents、metadatas 的 JSON 文件")
    parser.add_argument("--policy-file", default=None)
    args = parser.parse_args()
    try:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8-sig"))
        report = check_policy_sync(SnapshotCollection(snapshot), args.policy_file)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        parser.exit(1, "差异检测终止，未生成报告：" + str(exc) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
