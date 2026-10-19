# 服装店库存查询与原生Function Calling演示

发布验证更新：维护者已在Windows Python 3.12环境验证全项目351项自动化测试通过（1.217秒），并确认本地Qwen2.5:3b原生工具调用成功。此为维护者反馈，不是本轮文档审查复跑或推理性能指标。下文任务52—54中的Codex缺依赖记录为当时的环境限制，不表示当前用户环境仍未通过。总体作品介绍见[根README](../../README.md)，经营诊断案例见[案例文档](../../docs/store_agent_case_study.md)。

所有库存均为完全虚构的学习数据，不代表真实门店，不连接企业系统。基础查询完全离线；任务53新增显式运行的本机Qwen原生Function Calling入口，不涉及RAG、网页、API或数据库。

## 文件职责

- `fictional_inventory.py`：六条虚构库存，以只读映射保存。
- `schemas.py`：Pydantic v2参数模型与共享字段约束。
- `tools.py`：`query_inventory(product, color, size)`只读查询。
- `test_inventory.py`：14项离线单元测试（部分包含多个子用例）。
- `assistant.py`：唯一工具登记、参数与结果校验、两轮模型调用、权威事实摘要。
- `cli.py`：显式调用本机Ollama的命令行入口；默认不展示模型原始回复。
- `test_assistant.py`：19项Fake模型离线测试，每项检查库存未被修改。
- `grounding.py`：根据原始提问，用有限完整语法核对商品、颜色、尺码。
- `test_grounding.py`：8项不依赖Pydantic的来源核对测试；任务54另给助手测试新增6项来源保护测试，助手测试现为25项。
- `__init__.py`：包标记；上级`demos/__init__.py`使根目录unittest发现可递归进入此包。

| 商品 | 颜色 | 尺码 | 件数 |
| --- | --- | --- | ---: |
| 基础T恤 | 白色 | M | 8 |
| 基础T恤 | 白色 | L | 0 |
| 基础T恤 | 黑色 | M | 3 |
| 休闲衬衫 | 蓝色 | M | 5 |
| 休闲衬衫 | 蓝色 | L | 0 |
| 直筒长裤 | 黑色 | L | 2 |

## 输入与结果

三个参数必填且必须为字符串；strip后商品、颜色长度1至80字符，尺码只接受大写S/M/L/XL。缺失、额外字段、空白或错误类型抛出`pydantic.ValidationError`，不转换成缺货。名称精确匹配，不猜测同义商品，不自动把小写尺码转成大写。

查询顺序是：先确认商品存在，再确认颜色尺码记录存在，最后读取数量。

| status | 含义 | quantity |
| --- | --- | --- |
| in_stock | 记录存在且数量大于0 | 正整数 |
| out_of_stock | 记录存在且数量为0 | 0 |
| product_not_found | 商品不存在 | None（JSON中为null） |
| variant_not_found | 商品存在，颜色尺码组合无记录 | None（JSON中为null） |

结果还有product、color、size及`data_source="fictional_demo"`。不能通过`if not quantity`判断记录是否存在，因为0也是Python假值。每次返回新字典，修改返回值不影响库存；只读映射的键和值也都是不可变类型。它用于防止意外修改，不是针对恶意Python代码的权限隔离。

## Windows PowerShell验证

在项目根目录、已有可用Python 3.12及Pydantic v2环境执行，不需要增加依赖：

```powershell
.\.venv\Scripts\python.exe -c "import pydantic; print(pydantic.__version__)"
.\.venv\Scripts\python.exe -m unittest demos.inventory_assistant.test_inventory -v
.\.venv\Scripts\python.exe -c "from demos.inventory_assistant.tools import query_inventory; print(query_inventory('基础T恤', '白色', 'M'))"
.\.venv\Scripts\python.exe -m unittest discover -v
```

第一条查询示例预期为in_stock、quantity=8，来源fictional_demo，不是模型生成结果。不要直接运行包内tools.py，使用上面的包导入方式。

## Function Calling调用关系

```text
cli.main → InventoryAssistant.ask
  → chat(tools=[QUERY_TOOL]) → 原生message.tool_calls
  → 检查唯一工具名 → InventoryQuery验证参数 → 原始问题支持核对
  → query_inventory（最多一次）→ 验证结果结构
  → 保存assistant工具调用消息 + role=tool的JSON结果
  → chat（不传tools，最多第二轮）
  → Python事实摘要作为final_text；模型回复单独保存
```

模型正文中的JSON不会当作tool_calls解析；无原生调用就不查询，提示明确商品、颜色、尺码。未知工具、多个调用、缺字段、错误类型或无效尺码均拒绝。只有白名单函数会被Python显式执行，不使用eval或动态导入模型指定函数。

`assistant.py`中真正执行查询的是 `raw_result = query_inventory(**arguments.model_dump())`：model_dump将校验后的参数变成字典，`**`将字典键值展开为函数参数。它复用原只读工具，没有重写库存逻辑。

第二轮将第一轮assistant消息原样保留，再追加 `{"role":"tool","tool_name":"query_inventory","content":"JSON结果"}`。该消息格式依据[Ollama官方工具调用文档](https://docs.ollama.com/capabilities/tool-calling)，实际客户端0.6.3与本地模型的兼容性仍须实测。

## 展示与结构化状态

`ask()`返回status、tool_requested、requested_arguments、validated_arguments、arguments_validated、tool_executed、query_result、model_reply、fact_summary、final_text、model_calls。

- `tool_executed`表示已尝试执行Python函数；仅query_result非空表示取得并验证了结果。
- 状态包括ok、fallback、needs_input、invalid_arguments、unknown_tool、multiple_tools_rejected、invalid_response、model_error、tool_error、invalid_question。
- 默认final_text始终由Python模板产生，保留0库存和不存在的区别。模型回复不具有覆盖数量的权限，也不以简单文字匹配冒充语义验证。
- 第二轮失败、空回复、结构错误或再次请求工具时，返回fallback和已取得的Python事实，不继续执行。
- 首轮失败或查询未成功时，不编造数量。默认输出固定提示，不暴露内部异常堆栈。
- trace仅用于学习，包含模型请求参数和未经语义验证的模型原文，不应视为权威事实或用于真实企业数据。

## 离线测试及真实验证命令

以下命令供维护者在已有Windows Python 3.12.0环境手动执行；本次没有安装依赖或调用真实Ollama。

```powershell
# 只运行离线测试，不触发模型
.\.venv\Scripts\python.exe -m unittest demos.inventory_assistant.test_inventory demos.inventory_assistant.test_assistant -v
.\.venv\Scripts\python.exe -m unittest discover -v

# 查看版本与本地模型，不生成回答
ollama --version
ollama list
ollama show qwen2.5:3b

# 以下才是真实推理，请明确手动执行
.\.venv\Scripts\python.exe -m demos.inventory_assistant.cli '基础T恤白色M码还有几件？'
.\.venv\Scripts\python.exe -m demos.inventory_assistant.cli '基础T恤白色L码还有吗？' --trace
.\.venv\Scripts\python.exe -m demos.inventory_assistant.cli '羽绒服黑色M码有货吗？' --trace
.\.venv\Scripts\python.exe -m demos.inventory_assistant.cli '基础T恤红色M码有货吗？' --trace
.\.venv\Scripts\python.exe -m demos.inventory_assistant.cli '基础T恤有货吗？' --trace
```

`--trace`可观察真正tool_calls是否产生、模型参数是否正确、是否验证、查询结果和模型调用次数。任务54增加argument_support字段，区分参数格式通过与原始提问支持通过；仍不宣称解决通用自然语言规格溯源。

## 任务54：执行前核对原始提问

Pydantic验证“白色、M”等值是否格式合法；`check_argument_support`验证原始用户问题是否明确表达了同一组条件。核对仅使用原始question和确定性规则，不读取模型的解释来证明用户意图。核对失败返回needs_input，tool_executed为false，不调用库存工具或第二轮生成；最多一次查询及Python权威摘要保持不变。

自由表达只识别三个演示商品名称，颜色为数据中的白色/黑色/蓝色以及显式红色，尺码为S/M/L/XL。支持空格、小写尺码规范化、M码/M号、有限查询前后缀及两种顺序，例如：

- `请问基础T恤白色M码还有几件？`
- `查一下基础T恤 白色 m 码库存`
- `白色M码的基础T恤有货吗？`
- `商品：基础T恤，颜色：白色，尺码：M`

显式字段格式还允许未收录的商品或颜色，如 `商品：羽绒服，颜色：白色，尺码：M`，随后由原工具判断商品不存在，不改变查询语义。自由表达中的未知商品则保守追问。模型把黑色换成白色、把L换成M或换商品时，即使格式合法也拒绝执行。

这是完整匹配的有限语法，不是通用中文理解：缺条件、多规格、否定、选择、假设、示例、引号和改口表达均保守拒绝；暂不处理“不是白色，要黑色”、中码、大一码、同款、上一句规格、昵称及任意词序。含这些字符的真实商品名也可能被保守拒绝。未知表达请改为上面的显式字段格式，不会为了提高识别率自动猜测。

```powershell
.\.venv\Scripts\python.exe -m unittest demos.inventory_assistant.test_grounding demos.inventory_assistant.test_assistant demos.inventory_assistant.test_inventory -v
.\.venv\Scripts\python.exe -m unittest discover -v
```

真实客户端固定连接 `http://127.0.0.1:11434`，每次HTTP调用使用30秒网络超时，不自动下载模型、不自动重试，最多两次请求和一次工具执行。网络超时不是精确的整个任务墙钟截止时间；注入chat用于测试时由Fake负责行为。单次提问最多2000字符，不支持跨轮记忆或批量规格查询。

## 验证记录与边界

用户已报告：Windows Python 3.12.0环境中任务52的14项库存测试及全项目205项全部通过。本次不将Codex环境限制解释为用户虚拟环境损坏。

任务53在Codex执行环境中：可用Python为3.8.2，缺少Pydantic与FastAPI，项目解释器路径在此环境不可用。8个demo Python文件通过AST语法检查；新增19项测试已定义但未执行。全量发现记录为181项，178项通过、3项导入错误、0项断言失败，错误来自库存、助手测试缺Pydantic及API测试缺FastAPI。未通过的加载占位项不等于实际测试方法数量。

未执行真实Qwen推理。需用户本机验证SDK、服务端、模型模板是否返回原生tool_calls，及中文参数提取稳定性。离线覆盖已编写，不等于本次已运行通过；本地工具调用成功也不等于生产部署或安全压力测试完成。所有功能仅处理虚构数据，没有任何库存写入能力。

任务54验证更新：用户已报告此前全项目224项通过，且已验证真实Function Calling正常8件、0库存、商品不存在及空参数拒绝。本轮不运行真实Ollama。Codex环境中8项标准库来源核对测试全部通过，10个demo Python文件通过AST检查；全量发现189项，186项通过、3项依赖导入错误、0项断言失败。助手集成测试缺Pydantic，API测试缺FastAPI，不能宣称本轮全量通过；环境差异不表示用户虚拟环境损坏。保留原有全部助手测试，仅为原有0库存、未知商品和未知规格用例补上对应完整用户条件，原断言不变。
