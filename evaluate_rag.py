"""默认只校验题集；真实评测必须显式启用且仅使用独立数据库副本。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
import uuid

from policy_loader import load_policy_documents


ROOT = Path(__file__).resolve().parent
STATUSES = {"ok", "guarded", "needs_input", "no_policy", "error", "manual_review"}


def load_cases(path=None):
    cases = json.loads(Path(path or ROOT / "eval_cases.json").read_text(encoding="utf-8-sig"))
    known = {p["policy_id"] for p in load_policy_documents()}
    if not isinstance(cases, list) or not cases:
        raise ValueError("评测集必须是非空列表")
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("案例必须是对象")
        for key in ("case_id", "question", "category", "expected_status"):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError("案例缺少有效字段：" + key)
        if case["case_id"] in seen:
            raise ValueError("案例编号重复")
        seen.add(case["case_id"])
        for key in ("expected_policy_ids", "expected_facts", "forbidden_claims"):
            values = case.get(key)
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError("案例列表字段错误：" + key)
        ids = case["expected_policy_ids"]
        if len(ids) != len(set(ids)) or not set(ids) <= known:
            raise ValueError("预期政策编号重复或未知")
        if case["expected_status"] not in STATUSES:
            raise ValueError("未知预期状态")
        if case["expected_status"] == "manual_review" and not case.get("review_note"):
            raise ValueError("不确定状态必须说明人工复核原因")
    return cases


def evaluate_cases(service, cases):
    rows = []
    for case in cases:
        start = perf_counter()
        try:
            result = service.answer_question(case["question"])
            if not isinstance(result, dict) or result.get("status") not in STATUSES - {"manual_review"}:
                raise ValueError("服务返回结构错误")
            retrieved = {p["policy_id"] for p in result["retrieved"][:5]}
            final = {p["policy_id"] for p in result["final_policies"]}
            failure = False
        except Exception:
            result = {"status": "error", "answer": "", "message": "评测请求异常，未取得完整结果"}
            retrieved, final, failure = set(), set(), True
        expected = set(case["expected_policy_ids"])
        rows.append({
            "case": case, "result": result, "elapsed_seconds": perf_counter() - start,
            "request_failed": failure,
            "recall_at_5": len(expected & retrieved) / len(expected) if expected else None,
            "final_policy_recall": len(expected & final) / len(expected) if expected else None,
            "final_all_expected_hit": expected <= final if expected else None,
            "status_match": result["status"] == case["expected_status"] if case["expected_status"] != "manual_review" else None,
            "human_review": {"verdict": "pending", "notes": "逐项审核预期事实、禁止结论和引用；自动指标不评价语义正确性"}
        })
    return rows


def summarize(rows):
    def metric(key):
        values = [row[key] for row in rows if row[key] is not None]
        return {"value": sum(values) / len(values) if values else None, "denominator": len(values)}
    return {
        "total_cases": len(rows), "recall_at_5_macro": metric("recall_at_5"),
        "final_policy_recall_macro": metric("final_policy_recall"),
        "final_all_expected_hit_rate": metric("final_all_expected_hit"),
        "status_match_rate": metric("status_match"),
        "average_response_seconds": {"value": sum(r["elapsed_seconds"] for r in rows) / len(rows) if rows else None, "denominator": len(rows)},
        "request_failures": sum(r["request_failed"] for r in rows),
        "error_status_count": sum(r["result"]["status"] == "error" for r in rows),
        "semantic_accuracy": "未自动计算，所有答案待人工审核"
    }


def build_service(test_db_path, rerank_threshold=0.5):
    from main import validate_test_database
    path = validate_test_database(test_db_path)
    documents = load_policy_documents()
    import chromadb
    import ollama
    from sentence_transformers import CrossEncoder
    from rag_service import RagService
    client = chromadb.PersistentClient(path=str(path))
    collection = client.get_collection(name="company_policy")
    return RagService(collection, CrossEncoder("BAAI/bge-reranker-base"), documents, chat=ollama.chat,
                      rerank_threshold=rerank_threshold)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", "--cases-path", dest="cases", type=Path, default=ROOT / "eval_cases.json")
    parser.add_argument("--run", action="store_true", help="显式启用真实评测")
    parser.add_argument("--test-db-path")
    parser.add_argument("--rerank-threshold", type=float, choices=(0.5, 0.45, 0.4), default=None)
    args = parser.parse_args(argv)
    if args.run and not args.test_db_path:
        parser.error("真实评测必须提供 --test-db-path")
    if args.test_db_path and not args.run:
        parser.error("提供路径时还必须显式指定 --run")
    if args.rerank_threshold is not None and not args.run:
        parser.error("阈值实验必须显式指定 --run 和 --test-db-path")
    try:
        cases = load_cases(args.cases)
        if not args.run:
            print(f"静态校验通过：{len(cases)}题；未初始化数据库或模型。")
            return 0
        threshold = 0.5 if args.rerank_threshold is None else args.rerank_threshold
        service = build_service(args.test_db_path, rerank_threshold=threshold)
        rows = evaluate_cases(service, cases)
        report = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "cases_path": str(args.cases.resolve()),
            "cases_sha256": hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
            "policy_sha256": hashlib.sha256((ROOT / "knowledge_base" / "company_policy.txt").read_bytes()).hexdigest(),
            "model": "qwen2.5:3b", "reranker": "BAAI/bge-reranker-base",
            "rerank_threshold": service.rerank_threshold,
            "notes": "耗时仅含单次问答，不含初始化；模型生成可能不确定；失败案例计入适用指标分母并按未命中统计。",
            "summary": summarize(rows), "cases": rows
        }
        directory = ROOT / "eval_reports"
        directory.mkdir(exist_ok=True)
        target = directory / ("eval_" + uuid.uuid4().hex + ".json")
        with target.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        print("报告：", target)
        return 1 if report["summary"]["error_status_count"] else 0
    except Exception:
        print("评测终止：题集、测试路径或资源不可用；未回退正式数据库。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
