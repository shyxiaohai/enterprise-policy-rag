# 本地 FastAPI 接口

API只适配现有 `RagService.answer_question()`，不修改检索、0.50阈值、业务保护或引用校验。`service_resources.py` 负责公共初始化与整次问答锁，Streamlit继续保留自己的资源缓存与重绘检查。

## 安装与验证前提

目标环境为Python 3.12。以下命令供维护者手动执行，本次没有安装依赖、启动服务器或调用真实模型。

```powershell
py -3.12 --version
# 使用已有可用虚拟环境；首次安装请先按README创建环境。
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -v
```

FastAPI、Pydantic v2、Uvicorn、HTTPX依赖使用版本范围；维护者已验证实际本地安装组合，不代表范围内所有版本均兼容，也不是传递依赖锁文件。HTTPX供FastAPI TestClient使用，测试运行器仍为标准库unittest。

维护者已在真实Windows Python 3.12.0环境完成以下验证（本轮仅核对文档，未重新运行）：

- `pip check`：`No broken requirements found`。
- 原有178项测试与新增13项API测试，共191项全部通过。
- `GET /health`：HTTP 200，`service_initialized=true`。
- 普通住宿问答：HTTP 200，二线城市400元/天，引用 `hotel_002`。
- 餐饮超额保护问答：HTTP 200，`status=guarded`，引用 `meal_001`，未擅自判断160元能否全额报销。

以上为本地测试和接口验证，不代表已完成全新环境复现、生产部署或安全压力测试；H09及下文风险仍然存在。

## 启动

先按README准备Ollama、模型缓存，以及**已经存在的独立示例数据库**。仓库不包含数据库副本，政策文本本身不等于数据库已建成。API不负责建库或同步。

在项目根目录运行：

```powershell
$env:POLICY_API_TEST_DB_PATH = (Resolve-Path .\rag_demo_copy).Path
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

仅监听本机，单worker，不开启reload。配置路径须通过 `main.validate_test_database()`，拒绝正式 `chroma_db`、其子目录及不安全链接等。缺配置或初始化失败时保留HTTP进程，但health和ask返回503；修改配置后须重启。

lifespan初始化一次资源，BGE可能在首次加载时下载缓存。没有问答时不会主动调用Qwen；导入 `api.py` 本身不初始化数据库或模型。Chroma PersistentClient不提供本项目可保证的严格文件只读性，因此只能使用独立副本。不要让网页和API同时使用同一份副本；各进程的锁互不共享。

## GET /health

```powershell
Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8000/health'
```

成功HTTP 200：

```json
{"status":"ok","service_initialized":true}
```

未就绪HTTP 503，`status` 为 `unavailable`，`service_initialized` 为false。健康检查不发起模型生成，不探测Ollama实时可用性，不保证数据库与政策内容已同步。启动资源加载完成后才能接收该请求。

## POST /ask

请求必须仅含question字符串；去除两端空白后长度1至2000字符。数字、布尔值、null、额外字段、空白输入、超长或无效JSON均返回422，不回显原始校验输入。

下面命令会真正提交问答，可能调用本地Qwen，仅供维护者手动验证：

```powershell
$body = @{ question = '二线城市出差住宿和餐饮标准分别是多少' } | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/ask' -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

响应结构示例（非本次运行结果）：

```json
{
  "status": "ok",
  "answer": "现有服务返回的回答及政策依据",
  "policies": [
    {"policy_id":"hotel_002","source":"company_policy.txt","content":"二线城市出差住宿报销标准为400元/天。"}
  ],
  "message": ""
}
```

只从 `result["policies"]` 取得引用，不使用 `final_policies`。不公开候选、分数、补检索和diagnostics；所有公开文本字段处理常见绝对路径，处理不会改动原结果。编号与引用真实不等于语义全部正确，H09和回归集重复使用等原有限制继续存在。

| HTTP | 含义 |
| --- | --- |
| 200 | 核心返回ok、guarded、needs_input、no_policy |
| 422 | 请求不合法 |
| 503 | 初始化未就绪或核心返回error |
| 500 | 调用抛出未预期异常或结果结构不合法 |

ask错误也返回status、answer、policies、message四个字段，正文和政策为空，消息为固定通用提示，不返回内部异常。health只表达初始化状态，不采用ask的响应结构。

## 调用关系与代码阅读

```text
lifespan → configured_service → build_service
  → validate_test_database → load_policy_documents
  → 既有Chroma集合 + BGE + ollama.chat → SharedService(RagService)

POST /ask → AskRequest → SharedService.answer_question
  → 锁 → RagService.answer_question → AskResponse字段白名单 → JSON
GET /health → 检查已初始化服务对象，不调用answer_question
```

`api.py` 的POST接口逐行理解：

- `@application.post(...)`：把POST地址和函数关联，并声明响应模型。
- `def ask(payload: AskRequest)`：同步接口；FastAPI用Pydantic校验JSON后传入payload。
- `service = application.state.service`：取得生命周期创建的共享对象。
- `if service is None`：没有资源就返回503，不创建临时服务或回退正式库。
- `try`：包含服务调用和输出结构校验。
- `result = service.answer_question(payload.question)`：将清理后的字符串交给原服务。
- `if result["status"] == "error"`：映射为503，忽略内部错误消息。
- `return AskResponse(...)`：只构造公开字段；政策列表推导式只取实际policies的三个键。
- `except Exception`：返回固定500提示，不包含异常正文。

`StrictStr`拒绝隐式类型转换，`ConfigDict(extra="forbid")`拒绝额外字段，`field_validator`负责strip与长度。`asynccontextmanager`和yield把初始化、服务期间和退出清理分开；同步问答仍由普通def调用。`Lock`只保护同一包装对象、同一进程，不使服务具备多worker共享资源能力。

框架依据：[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)、[Pydantic严格模式](https://docs.pydantic.dev/latest/concepts/strict_mode/)。

## 离线接口测试及边界

```powershell
.\.venv\Scripts\python.exe -m unittest test_api -v
.\.venv\Scripts\python.exe -m unittest test_app -v
```

`create_app(service_factory=...)`允许生命周期注入Mock工厂，测试不连接数据库、不加载BGE、不调用Ollama。覆盖健康检查、初始化一次、输入校验、业务状态、错误映射、字段筛选、路径隐藏、结果不变及锁释放/串行执行。

没有认证、限流、请求体字节上限或模型任务取消机制；2000字符校验不是HTTP请求体大小限制。锁内排队会占用工作线程，断开请求不保证停止底层生成。仅用于可信本地应用，不应直接开放公网。应用不记录提问或正文，启动命令禁用访问日志，但第三方库仍可能输出自身日志。路径过滤不是密钥或个人资料的通用检测；政策授权及正文敏感性仍需人工确认。初始化失败不输出异常细节，排查须在维护者本地独立检查配置。
