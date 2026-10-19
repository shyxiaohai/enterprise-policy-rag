"""显式执行本模块才调用本机Ollama；默认只展示Python证据报告。"""

import argparse
import json

from .agent import StoreDiagnosisAgent


def main(argv=None, chat=None):
    parser = argparse.ArgumentParser(description="完全虚构的服装店经营诊断多工具Agent")
    parser.add_argument("question")
    parser.add_argument("--trace", action="store_true", help="展示结构化证据、参数来源和停止原因")
    args = parser.parse_args(argv)
    result = StoreDiagnosisAgent(chat=chat).ask(args.question, trace=args.trace)
    print(result["final_text"])
    print(f"调查状态：{result['status']}；停止原因：{result['stop_reason']}")
    if args.trace:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
