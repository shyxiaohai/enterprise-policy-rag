# 系统架构与执行顺序

以下对应 `RagService.answer_question()` 当前实现，不代表通用RAG框架。网页先安全校验已有独立数据库路径、加载缓存资源，再在表单提交时调用服务；会话重绘只显示最近结果。

```mermaid
flowchart TD
    UI[Streamlit表单提交] --> Scope[明确主题排除处理 scope_question]
    Scope --> Amb{排除范围不明确?}
    Amb -->|是| G[guarded安全说明]
    Amb -->|否| City[检索条件判断 单城市where / 多城市不缩为单档]
    City --> Retrieve[ChromaDB Top 5 初次召回 retrieved]
    Retrieve --> Empty{候选为空?}
    Empty -->|是| NP[no_policy]
    Empty -->|否| Rank[BGE交叉编码器评分排序 reranked]
    Rank --> Filter[默认0.50阈值 城市条件 排除主题筛选]
    Filter --> Need{有限缺失子主题满足补检索规则?}
    Need -->|是| Sub[餐饮标准或指定城市住宿标准 独立查询]
    Sub --> SubRank[Chroma Top 5与BGE重新评分]
    SubRank --> SubFilter[同一阈值 条件 相关性 内容及来源核对]
    SubFilter --> Merge[按政策ID合并去重 记录subquery_retrievals]
    Need -->|否| Final[final_policies快照]
    Merge --> Final
    Final --> Conditions{必要条件是否完整?}
    Conditions -->|否| Input[needs_input城市追问]
    Conditions -->|是| Available{最终证据为空?}
    Available -->|是| NP
    Available -->|否| Multi{多城市住宿?}
    Multi -->|是| MultiAnswer[展示已取得城市原文 不补数字 guarded]
    Multi -->|否| Guards[依次执行餐饮风险 住宿金额 有限证据保护]
    Guards --> Handled{规则已处理?}
    Handled -->|是| G
    Handled -->|否| Partial{餐饮与健身明确联合问法?}
    Partial -->|证据可用| G
    Partial -->|未处理| Relevant[定位相关政策]
    Relevant --> Excluded{已处理明确排除主题?}
    Excluded -->|是| G
    Excluded -->|否| Original{明确要求原文?}
    Original -->|是| Direct[有限原文回退匹配与证据核对]
    Direct -->|通过| OK[ok]
    Direct -->|未通过| G
    Original -->|否| Prompt[使用final_policies构造提示词]
    Prompt --> Qwen[Ollama Qwen2.5 3B生成]
    Qwen -->|调用失败| Error[error]
    Qwen -->|成功| Validate[来源 编号 相关性 显式原文校验]
    Validate -->|通过| OK
    Validate -->|失败| Discard[丢弃模型正文 记录原因]
    Discard --> Fallback[有限简单问法 Python可信原文回退]
    Fallback -->|证据完整且真实| OK
    Fallback -->|未匹配或证据不足| G
    G --> Display[Streamlit安全结果与引用卡片 调试区折叠]
    OK --> Display
    NP --> Display
    Input --> Display
    MultiAnswer --> Display
    Error --> Display
```

## 关键分支

必要城市缺失检查在初次检索、筛选及有限补检索之后执行。检索前的城市解析用于构造where，不能把它画成“缺城市立即追问”。多城市路径跳过单城市必需条件检查，当前总是保守展示已获得的城市原文，不承诺完整回答。

补检索仅覆盖已实现的两类条件：明确餐饮与另一支持主题联合且缺少餐饮候选；明确单城市住宿差额/余款等且缺少住宿候选。初次召回为空会提前返回，不进入补检索。补检索不查询固定政策编号、不使用用户金额计算实际赔付，每次独立评分仍须达到当前阈值。

保护分支及明确原文分支可以跳过Qwen。生成路径先生成再验证；失败才尝试有限回退。图中的Python规则回答不经过模型引用解析，不应被描述为所有分支都先生成或先验证。模型校验支持省略引用区原文，但显式提供的原文必须一致，最终统一展示候选完整原文。

## 数据和资源边界

| 字段/资源 | 含义 |
| --- | --- |
| `retrieved` | 首次Top 5召回顺序与距离，补检索不会改写它 |
| `reranked` | 首次BGE评分及排序，不等同于最终回答依据 |
| `final_policies` | 过滤及补检索合并后的候选，可能含无关高分记录 |
| `policies` | 服务随安全输出返回的依据；页面引用卡片只取此字段 |
| `subquery_retrievals` | 每次补检索的查询、召回、评分、接纳快照；网页目前显示数量摘要 |
| `diagnostics` | 引用失败原因、是否丢弃模型正文、部分回答来源；不是语义正确性认证 |

业务保护仍是有限规则。旧住宿金额分支会将最终候选列表作为返回依据，不能声称所有规则分支均有统一、全面的语义引用过滤。网页隐藏已知本地路径形态和异常详情，不替代发布前的人工脱敏审查。

`get_resources()` 使用Streamlit资源缓存；`SharedService`用锁串行保护共享问答。`validate_test_database()`检查副本安全性，`get_collection()`仅打开已有集合；政策写入由独立 `policy_sync.py` 管理，演示入口不自动调用。无多租户认证或生产权限系统。
