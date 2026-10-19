"""显式运行才调用本机Ollama；默认只打印权威事实和阶段状态。"""

import argparse
import json

from .assistant import InventoryAssistant


def main(argv=None, chat=None):
    parser = argparse.ArgumentParser(description="完全虚构的只读库存Function Calling演示")
    parser.add_argument("question", help="包含商品、颜色、尺码的一句提问")
    parser.add_argument("--trace", action="store_true", help="展示结构化阶段及未经语义验证的模型回复")
    args = parser.parse_args(argv)
    result = InventoryAssistant(chat=chat).ask(args.question)
    print(result["final_text"])
    print("阶段状态：" + result["status"])
    if args.trace:
        print("以下trace含模型原始回复，不是权威库存依据：")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["query_result"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
