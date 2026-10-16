# 企业AI智能知识库 v1.0

页面沿用现有RagService，默认Rerank阈值0.50。示例问题只填入输入框，点击“查询政策”才执行问答；页面重绘显示最近一次结果。中文状态区分已回答、安全保护、需要补充信息、本次证据不足、服务暂不可用。

最终回答引用来自服务的 `policies`，展示编号、原文件和完整原文；默认折叠的调试区分别展示初次Chroma召回、初次Rerank重排序、筛选合并后的 `final_policies`，分数保持原值。独立子主题补检索会单独标注数量。引用校验并不证明所有业务语义正确。

只打开经过已有安全校验的数据库副本和已有集合，不创建集合或同步政策。不要把正式 `chroma_db` 配置为演示副本。页面不展示配置路径、原始异常或被丢弃的模型正文。

Windows PowerShell在项目根目录启动（将第一行路径替换为已有独立数据库副本的实际路径）：

```powershell
$env:POLICY_APP_TEST_DB_PATH = 'D:\rag_eval_copy'
.\.venv\Scripts\python.exe -m streamlit run .\app.py --server.address 127.0.0.1 --server.port 8501
```

浏览器打开 `http://127.0.0.1:8501`。实际提交会使用本地模型；本轮实现与测试未调用真实Qwen。

离线模拟测试：

```powershell
.\.venv\Scripts\python.exe -m unittest test_app -v
.\.venv\Scripts\python.exe -m unittest discover -v
```

已知限制：H09普通列车硬座的部分问法可能因引用校验失败返回保守拒答；本轮不修改后端。有限主题规则不覆盖所有复杂表达。演示仅绑定本机，不包含多用户认证或生产部署配置。
