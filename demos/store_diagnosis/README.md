# 虚构服装店经营诊断：只读工具与受控多工具Agent

发布验证更新：维护者已在Windows Python 3.12环境验证全项目351项自动化测试通过（1.217秒），并确认本地Qwen2.5:3b原生销售→补货→历史库存链路成功。这里的秒数是离线测试时间，不是模型推理性能。本轮只核对文档；下文各任务中的Codex依赖错误和较旧数量是历史开发记录，不代表当前维护者环境状态。面试阅读入口为[经营诊断案例](../../docs/store_agent_case_study.md)，整体运行入口见[根README](../../README.md)。

所有数据、店铺标识、时间、单号和金额均为完全虚构的演示内容，不对应任何真实深圳店铺。第一阶段的三个确定性只读工具保持不变；任务56新增可显式运行的本机Qwen多工具Agent。不接入API、数据库或企业系统，不提供库存扣减、采购审批等写入功能。

## 业务数据与口径

单店标识 `SZ-DEMO-001`，时区统一+08:00，资料截止时间2026-09-18 20:00。销售为两个完整7日，起点包含、终点不包含：P1是9月1日至7日，P2是9月8日至14日。

| 指标 | P1 | P2 |
| --- | ---: | ---: |
| 进店客流（人次） | 2100 | 2100 |
| 完成成交订单（单） | 630 | 490 |
| 成交率 | 30% | 23.3333% |
| 客单价（元） | 300 | 286 |
| 营业额（元） | 189000 | 140140 |

演示无退款；成交率=订单/客流，营业额=订单×客单价。原始金额及工具返回金额以整数“分”为单位，表格以元展示。百分数与天数按ROUND_HALF_UP四舍五入至4位小数，成交率变化为-6.6667**个百分点**，营业额下降48860元，变化率约-25.8519%；差值从未舍入值计算。不能把百分点当作百分比降幅。

商品目录同时提供P1销量（件）：基础T恤180、休闲衬衫120、直筒长裤40，供识别本演示中的相对热销商品；这是部分商品销量，不是全店订单明细。库存快照固定为9月14日20:00：基础T恤白色S/M/L/XL分别5/0/0/2件，休闲衬衫蓝色M/L分别0/4件，直筒长裤黑色L为6件。0库存记录提供缺货起点，未提供完整库存日流水。

基础T恤补货：9月8日申请、11日审批、12日采购，预计12日到货，实际16日到货，M/L申请及到货分别30/20件；审批等待3天、到货比预计晚4天。衬衫补货：11日申请、12日审批、13日采购，预计14日到货、实际17日到货，蓝色M为20件。

补货到货发生在库存历史快照之后，不能把后到货数加到历史快照或当作当前库存。工具返回证据编号和节点时间，允许比较先后关系，但不判断“审批或缺码导致销售下降”。缺少顾客需求、流失原因、日销售等数据，不能量化缺码造成的损失。

## 文件职责

- `fictional_data.py`：一套共同的原始数据；递归只读映射和元组，提供独立副本。
- `schemas.py`：Pydantic v2参数、记录与响应模型，核对金额、数量及时间节点。
- `tools.py`：只读查询和确定性计算；没有模型判断。
- `test_data.py`：6项标准库数据一致性测试。
- `test_tools.py`：18项工具测试，含错误参数、数据损坏、返回结构及跨工具一致性。
- `__init__.py`：包入口标记，导入不加载外部资源。
- `agent.py`：模型选工具，Python执行参数来源、结构、证据和预算校验；规则生成权威报告。
- `availability.py`：每轮从用户条件及已验证结果收集参数候选，复用原来源检查决定工具开放；不执行查询。
- `progress.py`：有限调查领域识别、verified事实完成度和尚未执行的候选判断，不查询业务数据。
- `cli.py`：命令行演示，`--trace`展示工具历史及参数来源。
- `test_agent.py`：64项Fake模型离线测试（含诊断、动态开放、完成度、来源保护及两类有界提醒），不导入或调用真实Ollama。
- `test_availability.py`：8项标准库候选收集测试；来源许可回调使用stub，不能替代Agent集成验证。
- `test_progress.py`：8项标准库完成度与候选判定测试。

## 三个工具

```python
query_sales(store_id="SZ-DEMO-001", baseline="P1", comparison="P2")
query_inventory(product, color, size, store_id="SZ-DEMO-001")
query_replenishment(product, store_id="SZ-DEMO-001")
```

参数必须为字符串，strip后非空，尺码为S/M/L/XL；额外字段拒绝。销售基期与比较期须不同，已知期间只能P1到P2。当前没有任意日期查询、实时库存、多店合并或批量查询，调查多个尺码可分别调用库存工具。

| 工具 | 解决的问题 | 主要返回事实 |
| --- | --- | --- |
| query_sales | 销售变化来自哪些可观测指标变化？ | 两期间原始数据、成交率、差额、计算依据、P1商品销量及证据ID |
| query_inventory | 指定规格在快照时有货还是缺货？ | 库存件数、缺货起点、快照时间及INV证据 |
| query_replenishment | 补货走到哪些节点、实际何时到货？ | 申请/审批/采购/到货单号、时间、规格数量、等待与晚到天数 |

所有结果包含data_source=fictional_demo、store_id、as_of、evidence_ids。工具返回前通过对应Pydantic响应模型验证，并转换为可JSON序列化的独立字典。

销售状态：ok、store_not_found、period_not_found；库存状态：in_stock、out_of_stock、product_not_found、variant_not_found、store_not_found。无商品或无规格的quantity为None，0库存为0。补货状态：ok、store_not_found、product_not_found、no_records（目录中商品存在，但没有补货记录）。无记录仅说明该虚构数据集未收录，不能推断永不补货。

非法参数抛出Pydantic ValidationError；数据读取、计算或响应验证失败抛出本模块QueryFailure，不伪装成“没有记录”，也不返回内部异常文本。该内存演示没有真实外部服务故障，失败路径通过测试模拟数据损坏验证。

## Windows PowerShell离线验证

在项目根目录使用已有Python 3.12与Pydantic v2环境，不安装任何新依赖：

```powershell
.\.venv\Scripts\python.exe -m unittest demos.store_diagnosis.test_data demos.store_diagnosis.test_tools -v
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -c "from demos.store_diagnosis.tools import query_sales; import json; print(json.dumps(query_sales(), ensure_ascii=False, indent=2))"
.\.venv\Scripts\python.exe -c "from demos.store_diagnosis.tools import query_inventory; print(query_inventory('基础T恤','白色','M'))"
.\.venv\Scripts\python.exe -c "from demos.store_diagnosis.tools import query_replenishment; import json; print(json.dumps(query_replenishment('基础T恤'), ensure_ascii=False, indent=2))"
```

以上不连接模型、网络或数据库。只读数据结构防止意外修改，不是恶意Python代码的权限沙箱。

## 为什么不能只靠RAG

RAG适合查政策或流程说明，例如补货审批要求；库存数量、销售计算和流程节点需要结构化查询与明确时间口径。把它们仅作为文字交给模型，容易混淆0与不存在、快照与当前、相关与因果。第一阶段先实现确定性工具；任务56在这些原工具之上新增模型选工具循环，工具业务语义保持不变。

## 验证记录

用户已报告既有项目在Windows Python 3.12环境238项测试通过，该记录不代表本轮新增测试已通过。Codex环境与用户环境不同；本轮实际结果见交付记录，不能据此判断用户虚拟环境损坏。未实测全新环境复现、生产部署或压力测试。

本轮Codex实际检查：6个Python文件通过AST语法检查；新增24项测试中，6项标准库数据测试全部通过，18项Pydantic工具测试因缺少依赖未执行。全量发现记录为196项，192项通过、4项模块导入错误、0项断言失败；导入错误为新工具及既有库存/助手测试缺Pydantic、既有API测试缺FastAPI。加载错误占位项不等于测试方法数量。没有安装依赖、连接模型或操作数据库。

## 任务56：模型选择工具的受控循环

单工具Function Calling主要解决一次查询；此处模型可以看到每次已验证结果，再决定下一工具及参数。Python不会自动按固定顺序执行三个工具。下面是一条允许的演示路径，不是写死的步骤：

```text
用户：最近营业额为什么下降？帮我调查销售、库存和补货环节。
 → Qwen选择query_sales()
 → Python验证并执行 → SALE-P1/P2及P1商品销量CAT-001等
 → Qwen从返回结果选择query_replenishment(product="基础T恤")
 → Python校验商品来自CAT-001 → 返回REP-001及白色M/L补货项
 → Qwen选择query_inventory(product="基础T恤", color="白色", size="M")
 → Python校验整组规格来自REP-001 → 返回9月14日快照INV-002
 → Qwen不再请求工具 → Python按已验证事实生成报告
```

只有`message.tool_calls`被识别为调用；不解析正文JSON。白名单REGISTRY绑定三组函数、参数模型及返回模型。每轮最多执行一个工具，多调用整批不执行；任务59允许结构完整的批次进行一次有界串行重新选择。通过验证后实际执行位置为 `raw = bounded_call(execute, deadline, **args)`，其中execute只可能来自白名单；bounded_call最终调用`function(**kwargs)`，不是模型执行Python。

成功执行后保留原assistant工具调用消息，再追加 `{"role":"tool","tool_name":工具名,"content":JSON工具结果}`。下一轮携带整个上下文，使模型能够读取结构化事实。消息协议参考[Ollama官方工具调用说明](https://docs.ollama.com/capabilities/tool-calling)。本机服务端/客户端兼容性必须另外真实验证。

### 参数来源规则

- `store_id=SZ-DEMO-001`及P1到P2是应用固定调查范围，来源明确记为application_scope，不伪装成用户输入；不允许模型切换其他店铺或期间。
- 用户直接条件只接受少量完整句式，例如`商品：基础T恤，颜色：白色，尺码：M`、`查询基础T恤的补货记录`。复杂否定、多选、未知句式不作为用户参数依据。若已有明确用户条件，后续工具证据不能覆盖它。
- 销售结果hot_products中的商品及CAT证据允许查询该商品补货，但**不能**授权猜测颜色尺码。
- 已验证补货记录内同一商品同一item的颜色尺码允许查询历史库存，记录REP证据和历史索引。不允许从不同商品或不同记录拼凑规格。
- 前次已验证库存记录也可提供完整规格。仅成功校验的结构化结果能成为来源，模型正文、失败结果及未知编号都不作为证据。
- 新工具若没有来源关系，安全停止unsupported_arguments，不偷偷给默认颜色或尺码。

这是有限、规则驱动的**执行许可校验**，不是通用中文语义理解。它约束参数从哪里来，不代表能完整判断用户所有意图。提示词建议调查路径，但工具选择和顺序由模型请求决定；报告结构、假设与next_checks则明确是Python规则模板，不宣称模型自主诊断因果。

### 预算、异常与时间边界

默认最多6次模型请求、5次工具执行尝试、整个任务等待预算90秒，可在StoreDiagnosisAgent构造时设置（硬上限分别10、10、180秒）。相同工具及归一化参数只执行一次；任务59批次串行重试也计入同一模型次数与时间预算，不允许无限重试。固定本机Ollama地址127.0.0.1:11434，单次网络超时最多30秒且不超过剩余预算。

模型和工具在受限等待的守护线程中执行，主流程等待超过任务预算即停止，不采纳迟到结果或安排下一工具。Python不能强制终止已开始的同步线程：底层网络请求或只读查询可能短暂继续，网络取消及服务器生成中止不作保证；线程启动前也会检查截止时间。本版不适合并发服务或不受信任的任意工具。次数和时间限制不等于生产级资源隔离。

未知工具、格式错误、未授权参数、多工具批次、重复调用、工具异常或输出结构错误均停止并保留此前证据。内部异常正文不进入默认输出。tool_history中的status区分attempted/verified/failed/timeout；失败或超时项没有可信result，不能成为后续参数来源或报告事实。

### 输出与报告

返回status、tool_history、evidence_ids、sales_findings、inventory_findings、replenishment_findings、hypotheses、next_checks、final_text；另含stop_reason和model_calls用于解释停止原因。

- completed：模型正常结束且用户明确要求的领域均取得至少一次可信结果；若未可靠识别明确领域，沿用三个领域均有记录的保守判定。不代表全店所有规格都调查完。
- partial：只取得部分可信事实，或因保护/预算停止。
- stopped：没有取得可报告的可信事实。

tool_history保存经过验证的参数、parameter_sources及实际调用结果；evidence_ids只合并已验证结果真实返回的编号。最终模型文字不作为权威数值、证据或因果结论，不进入final_text。销售与库存数值全部从工具结果读取，金额从分转换为元；Python模板将已确认事实、未证实假设和缺失证据分开。

9月14日历史库存为0不代表9月18日仍为0；9月16日到货不能加回9月14日库存。即使缺码与销售下降同时出现，也只能提出待检验假设；顾客流失、每日销量、促销与上架时间仍需进一步调查。

## 任务56运行与验证

用户已报告任务55后Windows Python 3.12全项目262项通过；Ollama 0.40.5和qwen2.5:3b的单工具调用已在用户环境验证。多工具链路仍需单独验证，本轮未执行真实推理，也没有安装依赖。

```powershell
# 全部为离线Fake模型测试
.\.venv\Scripts\python.exe -m unittest demos.store_diagnosis.test_agent -v
.\.venv\Scripts\python.exe -m unittest demos.store_diagnosis.test_data demos.store_diagnosis.test_tools demos.store_diagnosis.test_agent -v
.\.venv\Scripts\python.exe -m unittest discover -v

# 只查看服务端版本和模型列表
ollama --version
ollama list
ollama show qwen2.5:3b

# 以下是真实Qwen多工具推理：仅由用户明确手动执行
.\.venv\Scripts\python.exe -m demos.store_diagnosis.cli '最近营业额为什么下降？帮我调查销售、库存和补货环节。' --trace
```

trace应检查参数来源能否从CAT商品追溯到REP规格，再到INV历史快照；若模型选择未经支持的规格，本版会停止，而不是保证提示词一定使三步路径成功。没有完整路径时显示partial/stopped，不伪造调查成功。

任务56 Codex实际结果：3个新增Python文件通过AST语法检查；25项新Agent测试因缺少Pydantic未执行。全量发现197项，192项通过、5项依赖导入错误、0项断言失败；错误涉及Agent/工具/既有库存测试缺Pydantic及既有API测试缺FastAPI。未将用户262项记录当作本轮复跑结果，环境差异不意味着用户虚拟环境损坏。尚未验证本机多工具成功率、完整模型兼容性、生产部署或压力测试。

## 任务57：unsupported_arguments拒绝诊断

用户已报告经营诊断模块49项测试通过，销售工具真实查询成功；以下是针对补货提问失败的源码核对，不是重新执行真实推理。

`explicit_user_conditions()`采用整句fullmatch。短句`请查询基础T恤的补货记录`能提取product；完整句`请查询基础T恤的补货记录，检查补货申请、审批和到货时间是否存在延迟。`因多出的说明子句返回空字典。在无前置可信工具结果时，即使模型正确返回基础T恤，`parameter_sources()`也无法找到允许来源，返回None，随后停止unsupported_arguments。这是确定性的有限语法误拒路径；没有原始模型参数时，不能断言该次真实失败没有其他因素，如店铺不符或商品名不同。

合法来源仍是用户已被规则明确识别的商品，或此前验证通过的工具商品事实。本轮保留上述安全策略和参数核对函数，不扩展正则，不自动先查销售。最小修复建议留待后续任务评审：只增加经过明确限定的补货提问说明子句语法，继续拒绝否定、冲突商品和复杂范围；先用此轮trace确认实际参数再决定，不采用任意子串匹配放行。

`ask(question, trace=True)`才附加rejected_tool_call；CLI仅在`--trace`时传入该开关并打印诊断。默认输出不含诊断细节。诊断白名单只保留必要参数，隐藏常见绝对路径/凭据样式、嵌套对象及超长文本，不包含完整问题、模型对话、异常堆栈或环境路径；不是通用隐私识别器，请勿传入真实企业敏感资料。

假设模型正确传入商品的复现格式（不是原真实调用的已捕获trace）：

```json
{
  "rejected_tool_call": {
    "tool": "query_replenishment",
    "arguments": {"product": "基础T恤", "store_id": "SZ-DEMO-001"},
    "stage": "parameter_sources",
    "reason": "user_conditions_not_recognized_and_no_prior_evidence",
    "field_checks": {
      "product": {"value": "基础T恤", "user_value": null, "user_match": false,
                  "user_conflict": false, "tool_evidence_matches": []},
      "store_id": {"value": "SZ-DEMO-001", "source": "application_scope",
                   "expected": "SZ-DEMO-001", "matched": true}
    },
    "validation_errors": []
  }
}
```

stage还区分model_request、native_response_parse、native_tool_call_parse、tool_registry、pydantic_validation。Pydantic错误仅返回字段名与错误类型，不回显原输入或异常内容。原有stop_reason兼容保留，例如消息解析错误仍为model_error，但trace明确标为native_response_parse，不再与来源核对混淆。逐字段证据匹配用于解释，不能代替整组参数来源判断；执行许可仍由原parameter_sources作出。

```powershell
# 离线：新增诊断测试 + 原Agent及工具测试
.\.venv\Scripts\python.exe -m unittest demos.store_diagnosis.test_diagnostics demos.store_diagnosis.test_agent demos.store_diagnosis.test_data demos.store_diagnosis.test_tools -v
.\.venv\Scripts\python.exe -m unittest discover -v

# 以下会真实调用本机Qwen，仅由用户手动执行同一条原问题
.\.venv\Scripts\python.exe -m demos.store_diagnosis.cli '请查询基础T恤的补货记录，检查补货申请、审批和到货时间是否存在延迟。' --trace
```

本轮新增7项Fake Agent用例及6项标准库诊断测试。Codex环境中6项标准库诊断测试全部通过；直接提取当前源码原函数进行无模型复现，确认短句放行、完整句拒绝。5个新增/修改Python文件通过AST检查。32项Agent测试因缺Pydantic未执行；全量发现203项，198项通过、5项依赖导入错误、0项断言失败。本轮没有调用Ollama、安装依赖或修改业务工具/来源策略，不能宣称新增Fake集成测试已通过。

## 任务58：有限补货说明子句修复

用户已捕获真实参数为query_replenishment、product=基础T恤、store_id=SZ-DEMO-001，确认任务57的误拒确实发生在用户商品识别，而非Pydantic或店铺范围。用户另报告修复前Windows全项目300项通过；这不是本轮复跑记录。

本次仅扩展`explicit_user_conditions()`补货分支：增加`帮我查一下`前缀，以及逗号后两种可选说明——`检查补货申请、审批和到货时间是否存在延迟`或`看看审批和到货有没有延迟`。仍使用完整fullmatch、单一已知商品及原否定/改口保护，不接受任意后缀或仅凭商品子串放行。

现支持：

- `请查询基础T恤的补货记录。`
- `请查询基础T恤的补货记录，检查补货申请、审批和到货时间是否存在延迟。`
- `帮我查一下基础T恤的补货情况，看看审批和到货有没有延迟。`

修复后识别出product=基础T恤，原parameter_sources确认user_explicit后放行；若模型改查休闲衬衫，仍以conflicts_with_explicit_user_conditions拒绝。否定、多商品、改口、示例和无法完整匹配的复杂句继续保守拒绝。本次不支持所有中文同义表达，不自动裁剪或忽略未知说明。

不改变业务工具、store_id、白名单、预算、重复保护、证据来源机制和诊断开关。原误拒回归测试改为验证修复成功；新增3项测试方法（包含多个正反子用例），覆盖三种合法表达、8种不明确/否定/多商品表达及完整提问下错误商品，并核对拒绝诊断。

```powershell
# 专项离线Fake模型测试及诊断测试
.\.venv\Scripts\python.exe -m unittest demos.store_diagnosis.test_agent demos.store_diagnosis.test_diagnostics -v
# 全项目离线回归
.\.venv\Scripts\python.exe -m unittest discover -v
# 以下才会调用真实Qwen；本轮未执行
.\.venv\Scripts\python.exe -m demos.store_diagnosis.cli '请查询基础T恤的补货记录，检查补货申请、审批和到货时间是否存在延迟。' --trace
.\.venv\Scripts\python.exe -m demos.store_diagnosis.cli '帮我查一下基础T恤的补货情况，看看审批和到货有没有延迟。' --trace
```

真实回归重点是补货请求成功进入tool_history、product来源为user_explicit，并取得REP-001。若模型随后停止，仅调查补货返回partial/model_finished仍符合当前完整度定义，不代表修复失败；如果模型发出其他非法调用，仍会被原保护拒绝。

Codex实际验证：提取本次源码的原识别和来源函数，3个正例、8个反例和1个错误商品检查均通过；这只是函数级检查，不替代Fake集成测试。6项标准库诊断测试通过，2个修改的Python文件通过AST检查。35项Agent测试仍因缺Pydantic未执行；全量发现203项，198项通过、5项依赖导入错误、0项断言失败。没有安装依赖或调用模型；环境差异不意味着用户虚拟环境损坏，也不能据此宣称本轮全项目通过。

## 任务59：批量请求诊断与一次串行重试

unsupported_arguments表示一个请求的参数缺少允许来源；multiple_calls_rejected表示模型一次请求多个工具，尚未执行。用户已确认真实销售及补货单工具成功，但完整调查首轮出现多个tool_calls。本次没有将未执行的批次当作证据，也没有放宽来源规则。

处理流程：

1. 批次结构检查：每项须有合法function结构、字符串name和对象arguments。结构损坏直接停止invalid_tool_call；结构完整仅代表可解析，不代表工具已登记、参数通过Pydantic或来源授权。
2. 多调用整批不执行。批次assistant消息不加入执行对话，不产生伪造tool结果，也不进入tool_history或evidence_ids。
3. 若还有模型次数和时间，追加固定用户提示：“本轮最多选择一个工具，等待真实结果后再决定下一步”。不携带模型生成的批次内容作为证据。
4. 再调用一次模型，计入原计数器并使用同一截止时间。再次多调用即停止multiple_calls_rejected，不对新批次继续重试。
5. 返回单个请求后，照常检查白名单、Pydantic、用户/可信前置证据、重复调用和工具预算。批次里出现过某规格不构成来源。
6. 单个工具验证执行成功后才加入assistant/tool上下文，清除本次重试状态；下一决策点若又出现批次，可再有一次重新选择，仍受整个任务最多6次请求限制。没有硬编码销售→补货→库存顺序。

批次含未知工具或无依据规格时，批次内仍一个也不执行；允许模型重新选择合法请求，不意味着原请求获批。重试返回无工具的文本时不会强迫执行，可能no_tool_calls或partial/model_finished。重试出现超时、模型异常、非法参数时停止，保留之前证据。

### trace字段

仅`--trace`展示rejected_tool_batch（最近一次）与rejected_tool_batches（本任务有界批次记录列表），原有rejected_tool_call保持兼容，用于具体拒绝阶段。默认输出仍只有Python报告与停止原因。

```json
{
  "rejected_tool_batch": {
    "request_count": 2,
    "calls": [
      {"index":0,"tool":"query_sales","arguments":{},"structure_valid":true,
       "registered":true,"reason":"not_executed_requires_single_selection"},
      {"index":1,"tool":"query_replenishment","arguments":{"product":"基础T恤"},
       "structure_valid":true,"registered":true,"reason":"not_executed_requires_single_selection"}
    ],
    "omitted_count": 0,
    "structure_valid": true,
    "stage": "batch_selection",
    "reason": "expected_single_tool_call",
    "executed_count": 0,
    "retry_attempted": true,
    "retry_result": "single_tool_verified"
  }
}
```

这是格式示例，不是已捕获的真实Qwen trace。诊断最多显示10项，并用request_count/omitted_count说明截断；只列必要参数，隐藏常见路径、凭据、嵌套和超长值，不记录完整模型对话。未知工具单独标记tool_not_registered；结构错误标为native_tool_call_parse/malformed_tool_call_batch。参数及来源未校验的批次不能被视为“合法执行过”。

### 离线测试与真实回归

新增10项Fake Agent测试（Agent测试现45项）及3项标准库批次诊断测试（诊断现9项）。覆盖重试成功、重试仍多调用、模型预算不足、网络异常/超时、总截止时间、未知工具、无来源规格、上下文真实性、多个决策点及默认隐藏诊断；原有来源和只读断言保留。

```powershell
# 专项离线测试
.\.venv\Scripts\python.exe -m unittest demos.store_diagnosis.test_agent demos.store_diagnosis.test_diagnostics -v
# 全项目离线回归
.\.venv\Scripts\python.exe -m unittest discover -v
# 以下才是真实本机Qwen调用；本轮未执行
.\.venv\Scripts\python.exe -m demos.store_diagnosis.cli '最近营业额为什么下降？帮我调查销售、库存和补货环节。' --trace
```

Codex实际验证：9项标准库诊断测试全部通过；4个修改Python文件通过AST检查；全量发现206项，201项通过、5项依赖导入错误、0项断言失败。45项Agent测试因缺Pydantic未执行，不能声称Fake集成或全项目通过。用户此前300项记录不代表任务58/59后最新数量；请以上述本机回归实际输出为准。

仍需真实验证模型能否遵守串行提示。重试会消耗调查预算，可能导致model_limit；模型也可能提前结束而partial。90秒是主流程等待边界，不能保证终止已开始的底层线程或Ollama生成，旧限制继续存在。本轮未安装依赖、改业务工具或运行真实推理。

## 任务60：证据驱动的动态工具开放

用户已报告Windows此前316项测试通过；真实完整调查首轮同时提出销售与空参数库存/补货调用。工具全部展示会让小模型尝试尚无条件的查询；提示词本身不能保证它先取得商品和规格。本次在每次chat之前重新计算菜单，包含批次串行重试，不再固定传入三个工具。

| 当前已有条件 | 开放候选 |
| --- | --- |
| 宽泛经营问题，无明确商品/规格及前置证据 | 仅query_sales（固定单店P1/P2范围） |
| 用户明确补货商品，或销售已验证hot_products | query_sales及有商品来源的query_replenishment |
| 用户明确完整规格，或补货已验证同一商品同一item的规格 | 可开放query_inventory，同时保留其他有依据候选 |

`tool_availability(user, history, parameter_sources)`只收集候选参数，不读取新的业务数据、不执行查询。收集结果必须再通过原parameter_sources才能作为开放依据；失败记录、模型正文和被拒批次不能贡献参数。每组库存规格保持独立，不从不同商品或补货项拼接。用户明确条件仍优先约束候选。

Agent的while循环在每次请求前运行：

```python
availability = tool_availability(user, history, parameter_sources)
opened = [name for name in REGISTRY if availability[name]["opened"]]
```

随后仅为opened构造tools定义，并在工具描述中附上当前有依据的参数示例，帮助Qwen使用已知商品/规格。这里是“开放一个工具类型及有依据参数候选”，不是Python代替模型选择并执行；模型仍在候选工具间选择实际参数和结束时机。

工具开放与执行许可是两层保护：即使开放库存工具，也不允许模型把白色M码改成无依据的黑色M码。实际请求仍经过白名单、Pydantic、当轮opened检查、parameter_sources、重复保护、预算和结果校验。已登记但未开放工具返回tool_not_open，trace阶段为tool_availability；未登记仍unknown_tool；错误格式/空参数仍由Pydantic拒绝。原来源冲突拒绝与诊断保留。已执行工具仍可能在菜单中出现，重复参数继续由原重复保护拒绝，本版不自动重复执行。

模型违反菜单一次返回多个调用时，仍执行任务59的“整批不执行＋一次有界串行重试”，重试前重新计算菜单；未执行批次不开放新工具。既有默认6次模型请求、5次工具尝试、90秒等待预算不变。

### trace可观测性

仅`--trace`增加model_rounds，每项包含：

- model_call：真实模型请求序号（重试也计数）；
- opened_tools：该轮实际传入tools的名称；
- tool_availability：三种工具的opened、reason、有依据的候选参数和parameter_sources；
- model_selection：真实原生tool_calls中的工具名及必要参数，最多10项；
- selection_count/selection_omitted_count：请求数与截断数；
- response_kind：tool_calls、no_tool_calls或异常阶段，不输出模型正文。

候选与来源最多显示10项，敏感值沿用safe_value隐藏；不输出完整对话、路径或凭据。字段级诊断、批次诊断及权威报告均保留。默认输出不增加这些细节。

### 离线与真实验证

新增8项Fake Agent测试和8项标准库候选收集测试；旧用例保留，只将“无来源且工具未开放”的拒绝预期更新为tool_not_open，已开放工具的实际参数来源错误仍为unsupported_arguments。测试覆盖每轮菜单、直接库存/补货、逐步开放、错误实际参数、空参数、未开放调用、trace和提前结束；原批次、时间语义和只读测试继续保留。

```powershell
# 专项离线验证，不调用Ollama
.\.venv\Scripts\python.exe -m unittest demos.store_diagnosis.test_availability demos.store_diagnosis.test_agent demos.store_diagnosis.test_diagnostics -v
# 全项目离线回归，以实际输出数量为准
.\.venv\Scripts\python.exe -m unittest discover -v
# 以下为真实Qwen完整调查；本轮未执行
.\.venv\Scripts\python.exe -m demos.store_diagnosis.cli '最近营业额为什么下降？帮我调查销售、库存和补货环节。' --trace
```

本轮Codex结果：8项标准库候选收集测试及9项诊断测试共17项通过；4个新增/修改Python文件通过AST检查。全量发现214项，209项通过、5项依赖导入错误、0项断言失败。53项Fake Agent测试因缺少Pydantic未执行；不能称为已离线验证整个Agent。候选收集单元测试使用来源回调stub，真实来源与执行集成仍需上述Windows测试确认。用户316项记录不当作本轮结果。

本次没有增加“继续调查”提醒，也不会偷偷执行Python查询。模型提前结束仍返回partial（无事实时stopped），不会补造调查结果。有限中文语法仍可能误拒不支持表达，模型可能忽略菜单或填错候选参数，本机多工具成功率仍须验证。线程等待预算不是底层任务强制取消或生产级并发隔离；90秒后底层只读调用/生成可能短暂继续，迟到结果不采纳。历史快照、非因果说明等安全边界不变。

## 任务61：领域完成度与一次继续调查提醒

用户已报告Windows此前332项通过。最新真实调查已成功查销售、基础T恤补货，但在query_inventory已开放白色M/L候选时返回no_tool_calls，结果partial。模型不再选择工具是“提前结束”，并非已有销售或补货工具失败；不能据此把库存标记完成。

### Python如何判断任务范围和完成度

`progress.requested_domains()`识别明确的销售/库存/补货领域列表，例如“调查销售、库存和补货环节”，以及有限单领域查询，如“请查询基础T恤的补货记录”“请调查库存”。包含否定、选择、改口、假设等表达时保守返回空范围，不把“全面调查”等模糊表达擅自解释为三个领域。这个规则不是通用中文理解，不能保证任意复杂句子都识别正确。

`progress.completed_domains()`只读取实际history中status=verified的结构化结果：

- sales：ok、两个期间及可信变化数据；
- inventory：in_stock/out_of_stock、真实规格记录且数量不为None；0库存也算一次可信查询；
- replenishment：ok且至少一条可信流程记录。

未执行调用、模型自述、失败/超时、商品/规格不存在或no_records不计入已完成事实。完成度仅表示某领域至少取得一次可信查询，不表示所有商品/尺码或因果关系均已调查完。无记录可以作为查询状态展示，但不会伪装成取得该领域经营事实。

### 继续提醒的条件

仅在模型返回no_tool_calls时，检查用户明确要求的missing_domains，并查找当前已开放、具有来源且参数组合未在seen中执行过的候选。存在这样的候选，且模型/工具/时间预算均有余量时，本任务最多发送**一次**继续调查提醒。

提醒只列尚未完成的领域和合法工具名，不预填规格、不伪造结果、不直接执行函数。用户只要求销售或补货时，只按该领域判断完成，不强迫查询其他领域。用户只要求库存而没有可靠规格时，即使销售工具仍开放，也不会以销售替代用户要求或强迫执行库存。

继续提醒作为真实用户消息加入上下文，保留之前真实无工具assistant回复；该正文不作为证据来源。下一次请求重新计算动态工具列表，执行前照常进行Pydantic、来源、白名单、重复、预算与结果验证。继续提醒不会重置已经使用的批次串行重试，批次重试也不会重置继续提醒次数。

默认最多6次模型请求、5次工具执行尝试、同一90秒截止时间不变。提醒后的模型调用计入请求预算，实际工具执行仍计入工具预算；没有工具余量时不发提醒。模型仍不调用工具时返回partial/model_finished（没有事实则stopped/no_tool_calls），不继续劝说；预算不足分别model_limit/tool_limit/time_limit，已有事实保留。

用户只查询销售且成功时可以completed/model_finished；未取得库存则不能把三领域任务标记completed。默认老板报告只显示易读的明确任务范围和缺失领域，不输出内部计数、提示文本或完整模型对话。

### trace字段

仅`--trace`增加以下字段，与原model_rounds及拒绝诊断一起使用：

```json
{
  "requested_domains": ["sales", "inventory", "replenishment"],
  "completed_domains": ["sales", "replenishment"],
  "missing_domains": ["inventory"],
  "continuation_attempted": true,
  "continuation_result": "still_incomplete",
  "continuation_reason": "missing_requested_domains_with_supported_candidates"
}
```

示例表示提醒后仍未取得库存，不是本次真实运行截图或实测结果。成功取得所需结果并正常结束时continuation_result为completed_requested_domains；未提醒时为not_attempted，reason说明无明确范围、已完成、无可信新候选或预算不足。没有额外依赖或Agent框架。

### 离线测试及真实回归

新增11项Fake Agent用例、8项标准库progress测试，覆盖“销售＋补货后提前结束→一次提醒→库存成功”、仍不调用、单领域结束、无规格依据、错误参数、模型与工具预算不足、提醒不重置、两类重试组合及超时保留事实。此前批次、时间、只读与来源测试保留；旧提前结束用例更新为一次提醒后仍保持partial。

```powershell
# 专项离线测试，不连接Ollama
.\.venv\Scripts\python.exe -m unittest demos.store_diagnosis.test_progress demos.store_diagnosis.test_agent demos.store_diagnosis.test_availability demos.store_diagnosis.test_diagnostics -v
# 全项目离线回归，以实际输出数量为准
.\.venv\Scripts\python.exe -m unittest discover -v
# 以下会真实调用Qwen；本轮没有执行
.\.venv\Scripts\python.exe -m demos.store_diagnosis.cli '最近营业额为什么下降？帮我调查销售、库存和补货环节。' --trace
```

Codex实际结果：8项progress、8项availability、9项diagnostics共25项标准库测试通过；4个新增/修改Python文件通过AST检查。全量发现222项，217项通过、5项依赖导入错误、0项断言失败。64项Fake Agent测试因缺少Pydantic未执行，不能称为集成或全项目通过；用户此前332项不代表本轮数量。

尚不能保证模型收到提醒后选工具、选正确参数或在剩余预算内完成任务；仍可能partial、被来源规则拒绝或超时。未做真实Qwen多工具回归、生产部署或压力测试。领域和商品识别都是有限语法，线程等待截止不保证强制取消底层Ollama任务。9月14日库存、9月16日到货及9月18日资料截止口径不变，相关性仍不能当作因果证明。
