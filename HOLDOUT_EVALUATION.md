# 独立留出集

`eval_cases_holdout.json` 包含24道新编问题，与原25道题无重复问题文本。预期事实仅依据 `knowledge_base/company_policy.txt`；未规定事项要求明确无法确认，不代表公司绝对没有相应制度。原题集及政策保持不变。

本次只准备题集，不运行模型、不读取数据库。题目尚未用于调参；如果根据本题集修改规则或选择阈值，它就成为验证集，后续应另建未见过的最终留出集。有限题量不能证明0.40对所有新问题都安全。

H05–H08检查餐饮问题中的住宿干扰；H18检查餐饮交通联合问题的住宿干扰；H08、H23检查否定主题；H11、H15、H16、H19–H24检查审批、适用范围及无依据推断。多城市、复杂条件等案例标为 `manual_review`，仍检查政策召回，但不强制唯一状态。

报告的召回率只检查 `final_policies` 是否包含预期ID，不惩罚额外ID，也不能证明回答正确。逐题审核 `answer` 和实际引用 `policies`：是否完整、是否出现 `forbidden_claims`、是否误引不相关条款、来源和原文是否真实。尤其不能仅凭100%召回宣布0.40有效。

静态校验与全量测试（项目根目录，Python 3.12）：

```powershell
.\.venv\Scripts\python.exe evaluate_rag.py --cases-path eval_cases_holdout.json
.\.venv\Scripts\python.exe -m unittest discover -v
```

以下真实评测命令仅供本地后续执行，需要已经存在的独立数据库副本；替换路径为真实副本路径。不要使用正式 `chroma_db`。保留原有路径安全校验、城市限制、证据及引用校验，默认阈值仍为0.50。每次生成新的UUID报告，记录实际题集绝对路径、题集哈希和实际服务阈值。

```powershell
.\.venv\Scripts\python.exe evaluate_rag.py --cases-path eval_cases_holdout.json --run --test-db-path 'D:\rag_eval_copy' --rerank-threshold 0.50
.\.venv\Scripts\python.exe evaluate_rag.py --cases-path eval_cases_holdout.json --run --test-db-path 'D:\rag_eval_copy' --rerank-threshold 0.45
.\.venv\Scripts\python.exe evaluate_rag.py --cases-path eval_cases_holdout.json --run --test-db-path 'D:\rag_eval_copy' --rerank-threshold 0.40
```

三组使用同一题集和政策快照，比较哈希后再比较指标；模型输出可能不确定。人工审核完成前不要将低阈值部署为应用默认值。
