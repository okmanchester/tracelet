# 系统已知问题

本清单记录架构检查发现的问题、证据及后续验收条件，不代表已经批准修复方案。涉及系统长期架构选择时，按照 [ADR 规范](../adr/README.md)提出 Proposed；普通修复无需为了记录而创建 ADR。

## 检查基准与证据边界

- 检查日期：2026-10-18（Asia/Shanghai）。
- 业务代码基准提交：`e4efaef06fd1b9fc0a62147ca0bb7f8207fb0c6c`。本次文档落盘未修改业务代码。
- 检查范围：后端 API、Agent 编排、工具、会话、知识库、数据初始化、评估入口及前端通信。
- 源码确认：已检查相关控制流和数据定义；下面的文件链接指向工作区当前版本，符号用于定位历史实现。可用 `git show e4efaef06fd1b9fc0a62147ca0bb7f8207fb0c6c:<仓库相对路径>` 复核基准代码。
- 同日架构检查中，使用 tracelet conda 环境运行现有单元测试，结果为 **8 passed**。其范围仅为模型接入相关行为，不代表系统整体可靠，也不是覆盖率数据。
- 同日架构检查还以一次性内存 mock 验证了 6 个场景，见文末配置及断言。未连接真实数据库、Redis、模型或向量服务；mock 不证明真实依赖的故障表现。一次性验证未作为回归测试入库。
- 未执行真实依赖故障测试、数据库迁移或负载测试；没有可据此引用的吞吐、延迟、成本或生产故障数据。

单元测试可在项目根目录激活环境后，从 backend 目录复核：

```powershell
conda activate tracelet
Set-Location backend
python -B -m pytest -q -p no:cacheprovider
```

先前完整运行关闭了无关 pytest 插件自动加载，并显式加载 `pytest_asyncio.plugin`。Windows 沙箱曾阻塞于 asyncio 创建本机 socketpair；在允许该本机操作的环境中完成测试。这是检查环境限制，不作为项目缺陷或业务性能证据。

## 维护方式

- 编号 `ARCH-NNN` 稳定且不复用；解决或延期时保留原问题与证据。
- 状态：`Open`（未开始）、`In Progress`（处理中）、`Resolved`（已有实现和验收证据）、`Deferred`（明确延期并说明原因）。所有初始问题均为 Open。
- 优先级是本次检查的建议：P0 表示可能直接损坏或丢失数据，应先消除；P1 表示扩展相关业务前应修正；P2 表示在对应能力投入使用或规模扩大前处理。不是生产事故分级，也不表示实施授权。
- 每项“建议方向”均待讨论；关联 ADR 初始为“无”。创建 ADR 时双向关联，接受 ADR 不自动改变问题状态。
- 后续更新注明日期、实现提交或变更链接及实际验证结果。状态变为 Resolved 时必须给出验收证据；延期须记录决定依据和重评条件。

## 索引

| 编号 | 问题 | 优先级 | 状态 | 关联 ADR |
| --- | --- | --- | --- | --- |
| [ARCH-001](#arch-001) | 会话权限规则不一致 | P1 | Open | 无 |
| [ARCH-002](#arch-002) | Agent 结果与转人工行为脱节 | P1 | Open | 无 |
| [ARCH-003](#arch-003) | 写工具重试可能重复执行 | P1 | Open | 无 |
| [ARCH-004](#arch-004) | 会话记忆与持久化记录缺少恢复边界 | P1 | Open | 无 |
| [ARCH-005](#arch-005) | 会话并发与人工接管缺少协调 | P1 | Open | 无 |
| [ARCH-006](#arch-006) | 脱敏未覆盖输入、存储与全部出口 | P1 | Open | 无 |
| [ARCH-007](#arch-007) | 知识库发布与缓存版本不一致 | P1 | Open | 无 |
| [ARCH-008](#arch-008) | 初始化默认删表且缺少增量迁移 | P0 | Open | 无 |
| [ARCH-009](#arch-009) | 演示时间假设进入运行逻辑 | P1 | Open | 无 |
| [ARCH-010](#arch-010) | 金额和单价使用浮点类型 | P2 | Open | 无 |
| [ARCH-011](#arch-011) | 评估与业务数据、缓存未隔离 | P1 | Open | 无 |
| [ARCH-012](#arch-012) | 对话通信缺少完整的失败与取消语义 | P2 | Open | 无 |
| [ARCH-013](#arch-013) | 异步请求路径包含同步阻塞调用 | P2 | Open | 无 |
| [ARCH-014](#arch-014) | 关键业务不变量缺少持久化回归测试 | P1 | Open | 无 |

<a id="arch-001"></a>

## ARCH-001：会话权限规则不一致

- **状态 / 优先级 / 关联 ADR**：Open / P1 / 无。
- **现象**：会话列表对客户同时过滤 tenant_id 和 user_id，但详情、追加消息、聊天复用会话仅检查租户或内部身份；同租户其他客户的会话可被继续使用。
- **影响与优先级理由**：当前“自己的会话”列表语义与资源访问边界不一致，可能暴露同租户其他用户的消息或允许代发消息；增加入口前应统一。
- **代码证据**：[conversations.py](../../backend/app/api/conversations.py) 的 `list_conversations`、`get_conversation`、`add_customer_message`；[chat.py](../../backend/app/api/chat.py) 的 `_get_or_create_conversation`；[deps.py](../../backend/app/deps.py) 的 `assert_tenant_access`。
- **验证方式与结论**：源码确认；mock M5 已复现同租户、不同 user_id 的会话被复用。未执行真实 HTTP 越权测试。客户管理员是否需要租户范围访问仍需产品决定，不能把现有列表行为当成正式权限设计。
- **建议方向（待讨论）**：先确定角色与资源访问矩阵，再集中复用访问策略，覆盖查询、写入及工具入口。
- **验收条件**：权限矩阵明确；列表、详情、聊天、追加消息和反馈行为一致；同租户其他用户及跨租户访问按角色被允许或拒绝，并有回归证据。

<a id="arch-002"></a>

## ARCH-002：Agent 结果与转人工行为脱节

- **状态 / 优先级 / 关联 ADR**：Open / P1 / 无。
- **现象**：各 Agent 返回不同结果字段，Supervisor 用 `getattr` 解释；专业 Agent 全部失败时仍汇总出“转接人工”文案，却未建单或置 need_human。诊断 need_ticket 可以建单但不改变 need_human；RAG 无命中答案没有 card，汇总时被丢弃。
- **影响与优先级理由**：用户看到的承诺、工单和实际会话模式可能矛盾，后续扩展 Agent 或人工流程会继续继承这个歧义。
- **代码证据**：[supervisor.py](../../backend/app/agents/supervisor.py) 的 `build_graph` 内 `specialists_node`、`ticket_node`、`summarize_node`；[doc_rag.py](../../backend/app/agents/doc_rag.py) 的 `RagResult`、`answer`；[chat.py](../../backend/app/api/chat.py) 的 `chat`。
- **验证方式与结论**：源码确认；mock M1、M2、M3 已分别复现全失败、建单与模式分离、无命中解释丢失。是否允许“建单但继续 AI”应明确建模，而非隐含依赖两个布尔值。
- **建议方向（待讨论）**：统一 Agent 结果契约与失败分类，集中决策建单、人工模式及文案；只有动作成功才声称“已完成”。
- **验收条件**：全失败、部分失败、无命中、证据不足、建单失败及主动转人工均有确定结果；回复与真实动作一致；明确并验证建单与人工模式的关系。

<a id="arch-003"></a>

## ARCH-003：写工具重试可能重复执行

- **状态 / 优先级 / 关联 ADR**：Open / P1 / 无。
- **现象**：`execute` 把业务工具执行和 `_log_call` 放在同一重试块。写入成功后日志失败会重放工具；`create_ticket` 每次生成新 ID 并独立提交，缺少请求幂等约束。反馈转人工另有直接建单路径。
- **影响与优先级理由**：重试、重复提交或异常恢复可能产生重复工单；后续接入真实写操作前必须处理。
- **代码证据**：[registry.py](../../backend/app/tools/registry.py) 的 `ToolSpec`、`execute`；[ticket_tools.py](../../backend/app/tools/ticket_tools.py) 的 `create_ticket`；[tickets.py](../../backend/app/api/tickets.py) 的 `submit_feedback`。
- **验证方式与结论**：源码确认；mock M4 已验证成功写入后首次日志失败导致业务执行两次。真实数据库提交后连接中断的恢复行为待验证。
- **建议方向（待讨论）**：区分读写重试；日志失败不重放业务；为写操作设计持久化幂等键、唯一约束及结果查询，并统一建单入口。
- **验收条件**：同一业务请求重复提交、超时重试及日志失败均不会产生重复业务记录；能区分执行失败与结果未知，返回同一已提交结果。

<a id="arch-004"></a>

## ARCH-004：会话记忆与持久化记录缺少恢复边界

- **状态 / 优先级 / 关联 ADR**：Open / P1 / 无。
- **现象**：MySQL 保存消息，编排却只从 Redis 加载历史与实体，Redis 记忆 TTL 为 6 小时；人工及直接追加消息路径未更新 Redis。答案生成后的记忆、Trace 和成本写入串行等待，且位于图调用的异常兜底之外。
- **影响与优先级理由**：记忆过期后可能遗失仍在数据库中的上下文；答案已生成也可能因附属写入失败而无法返回。
- **代码证据**：[session.py](../../backend/app/memory/session.py) 的 `ENTITY_TTL`、`get_history`、`append_message`；[supervisor.py](../../backend/app/agents/supervisor.py) 的 `load_context`、`run`；[workbench.py](../../backend/app/api/workbench.py) 的 `human_reply`；[conversations.py](../../backend/app/api/conversations.py) 的 `add_customer_message`。
- **验证方式与结论**：源码确认；mock M6 已复现 Redis 历史写入失败阻止完成答案返回。TTL 到期后的真实恢复路径和断点恢复待验证。
- **建议方向（待讨论）**：明确会话事实来源及缓存重建路径；统一消息写入和上下文投影，区分必须提交的业务结果与可恢复的附属写入。
- **验收条件**：Redis 清空或过期后能恢复允许使用的历史；人工消息进入后续上下文；附属故障不丢失已提交结果，失败可追踪和恢复。

<a id="arch-005"></a>

## ARCH-005：会话并发与人工接管缺少协调

- **状态 / 优先级 / 关联 ADR**：Open / P1 / 无。
- **现象**：聊天开始时读取 transferred_to_human，模型调用结束后直接写助手结果；未见提交时重新检查接管状态或会话版本。同会话无服务端轮次串行控制；Redis 实体更新是读、合并、写。
- **影响与优先级理由**：并发请求可能基于同一旧历史回答或覆盖实体；人工接管期间的在途 AI 仍可能写回。属于待验证的竞争风险，不能据此声称已经发生串话事故。
- **代码证据**：[chat.py](../../backend/app/api/chat.py) 的 `chat`；[workbench.py](../../backend/app/api/workbench.py) 的 `takeover_conversation`、`human_reply`；[session.py](../../backend/app/memory/session.py) 的 `update_entities`。
- **验证方式与结论**：源码确认缺少上述协调；并发竞争尚未动态复现。后续可用可控 barrier 暂停模型调用，插入第二轮或人工接管，再释放并检查写回。
- **建议方向（待讨论）**：明确会话轮次、请求幂等及接管状态迁移，用提交版本检查或合适的协调机制保证顺序；避免在模型调用期间持有长数据库事务。
- **验收条件**：多标签页并发、重复发送、实体同时更新和中途人工接管均有明确规则；过时 AI 结果不会违反已生效的接管决定。

<a id="arch-006"></a>

## ARCH-006：脱敏未覆盖输入、存储与全部出口

- **状态 / 优先级 / 关联 ADR**：Open / P1 / 无。
- **现象**：聊天保存 body.message 并直接交编排；Trace 保存输入、输出摘要时只截断。正常安全节点处理回复及 card，但澄清、缓存命中直接结束，工单提示在安全节点之后追加。
- **影响与优先级理由**：最终回复脱敏无法保护此前已存储或外发的内容；出口规则依赖分支约定。需在更多渠道和数据源接入前明确边界。
- **代码证据**：[chat.py](../../backend/app/api/chat.py) 的 `chat`；[trace.py](../../backend/app/observability/trace.py) 的 `TraceCollector.step`；[supervisor.py](../../backend/app/agents/supervisor.py) 的 `intent_node`、`security_node`、结束边与 `run`；[ticket.py](../../backend/app/agents/ticket.py) 的 `create_from_context`。
- **验证方式与结论**：源码确认调用顺序与保存路径。未做真实敏感数据外发测试，也未证明某个具体分支已发生泄漏；后续用合成敏感标记拦截存储和模型边界验证。
- **建议方向（待讨论）**：定义输入、模型上下文、业务证据、日志与输出各自允许的数据；统一最终出口并保留必要的非敏感诊断关联信息。
- **验收条件**：正常、澄清、缓存命中、建单失败、异常兜底及人工回复路径均验证敏感标记的存储、外发和展示策略；错误信息同样受控。

<a id="arch-007"></a>

## ARCH-007：知识库发布与缓存版本不一致

- **状态 / 优先级 / 关联 ADR**：Open / P1 / 无。
- **现象**：全量 ingest 预热 embedding 后删除旧 collection，再逐篇生成及写入，最后登记 MySQL 元信息。BM25 是常驻进程缓存，`reset_bm25` 未见调用；语义缓存键只有 embedding 指纹与租户，没有知识库版本或过期设置；文档中心直接读取 Markdown。
- **影响与优先级理由**：中途失败可能留下部分索引；文档、向量、关键词索引和答案缓存可能引用不同版本。BM25 数据读取还默认限于 2000 条，不能保证扩容后完整。
- **代码证据**：[ingest.py](../../backend/app/rag/ingest.py) 的 `ingest_all`；[store.py](../../backend/app/rag/store.py) 的 `ensure_collection`、`all_chunks`；[retriever.py](../../backend/app/rag/retriever.py) 的 `_ensure_bm25`、`reset_bm25`；[semantic_cache.py](../../backend/app/cache/semantic_cache.py) 的 `key_for`、`put`；[docs.py](../../backend/app/api/docs.py) 的 `get_doc`。
- **验证方式与结论**：源码确认；真实中途失败、跨进程缓存失效及超过 2000 条的召回表现待验证，不记录虚构命中率。
- **建议方向（待讨论）**：构建、校验新知识版本后再发布；统一向量、关键词、文档展示、引用与答案缓存的版本，支持失败保留旧版和回退。
- **验收条件**：构建失败不损坏生效知识；发布后所有 worker 及缓存版本一致；引用可定位对应版本；大于默认条数时关键词索引仍完整。

<a id="arch-008"></a>

## ARCH-008：初始化默认删表且缺少增量迁移

- **状态 / 优先级 / 关联 ADR**：Open / P0 / 无。
- **现象**：Makefile 的 init-db 固定传 `--recreate`，setup 依赖 init-db；该分支执行 `Base.metadata.drop_all`。不传参数时仅 create_all，仓库未见业务 schema 的增量迁移流程。
- **影响与优先级理由**：在有业务数据的库上重复 setup 会删表；继续增加字段后缺乏保留已有数据的演进路径，应最先消除直接数据风险。
- **代码证据**：[Makefile](../../Makefile) 的 `init-db`、`setup`；[init_db.py](../../backend/scripts/init_db.py) 的 `main`；[pyproject.toml](../../backend/pyproject.toml) 的依赖及工具配置。
- **验证方式与结论**：源码确认删表路径；为避免破坏现有数据，本次没有执行 setup、删表或迁移。未来只在一次性测试库验证。
- **建议方向（待讨论）**：分开安全初始化、增量迁移、种子数据与显式重置；为 schema 演进选择合适的迁移机制。
- **验收条件**：对有数据的测试库重复安全初始化不丢记录；已有 schema 能按受管理的版本迁移；破坏性重置有独立且明确的入口和说明。

<a id="arch-009"></a>

## ARCH-009：演示时间假设进入运行逻辑

- **状态 / 优先级 / 关联 ADR**：Open / P1 / 无。
- **现象**：Key 过期判断固定使用 2026-06-15；两处建单 ID 含同一固定日期。“近 N 分钟”统计以最近 429 或日志为锚点，只设置下界，没有锚点上界。
- **影响与优先级理由**：接入真实数据后，“当前过期”和“最近调用”可能与用户理解不一致，统计也可能包含锚点之后的其他数据；上线真实诊断前需分离演示与运行语义。
- **代码证据**：[apikey.py](../../backend/app/tools/apikey.py) 的 `query_apikey_status`；[logs.py](../../backend/app/tools/logs.py) 的 `query_recent_call_stats`；[ticket_tools.py](../../backend/app/tools/ticket_tools.py) 的 `create_ticket`；[tickets.py](../../backend/app/api/tickets.py) 的 `submit_feedback`。
- **验证方式与结论**：源码确认固定日期及查询范围；真实时间窗口诊断待验证。种子脚本固定数据时间本身不是缺陷。
- **建议方向（待讨论）**：运行时间与演示时钟分开；明确实时窗口和历史事件窗口，统一时区、上下界与展示含义。
- **验收条件**：过期边界、跨日与时区、实时和历史窗口均可确定性验证；演示数据可复现且不改变正常运行的时钟语义。

<a id="arch-010"></a>

## ARCH-010：金额和单价使用浮点类型

- **状态 / 优先级 / 关联 ADR**：Open / P2 / 无。
- **现象**：`Plan.price_per_call`、`overage_price_per_call` 和 `Invoice.amount` 使用 `Mapped[float]`，账单工具直接返回这些值。
- **影响与优先级理由**：后续若承担金额计算或对账，需要明确精度及舍入，避免依赖二进制浮点的表示；本次没有发现或复现具体错误账单，因此列为真实计费能力完善前的风险。
- **代码证据**：[models.py](../../backend/app/models.py) 的 `Plan`、`Invoice`；[billing_tools.py](../../backend/app/tools/billing_tools.py) 的 `query_plan`、`query_bill`。
- **验证方式与结论**：源码确认字段类型；实际金额累计误差和数据迁移表现待验证，不声称现有账单已计算错误。
- **建议方向（待讨论）**：评估定点存储、货币与单价精度、舍入及序列化约定，并与实际计费来源对齐。
- **验收条件**：精度和舍入规则明确；边界金额、单价乘调用量、迁移前后对账及接口往返验证通过。

<a id="arch-011"></a>

## ARCH-011：评估与业务数据、缓存未隔离

- **状态 / 优先级 / 关联 ADR**：Open / P1 / 无。
- **现象**：`evaluate` 清理所有语义缓存及 routecache，再使用数据集租户调用真实 Supervisor；固定 eval 会话 ID 可在同一 Redis 中复用，链路可能建单并记录记忆、Trace 和成本。
- **影响与优先级理由**：与业务共用依赖时会影响缓存和业务记录；重复评估可能带入上一轮记忆，破坏评估条件。应在继续依赖评估结果前隔离。
- **代码证据**：[run_eval.py](../../backend/eval/run_eval.py) 的 `evaluate`；[eval.py](../../backend/app/api/eval.py) 的 `run_eval`；[supervisor.py](../../backend/app/agents/supervisor.py) 的 `run`；[semantic_cache.py](../../backend/app/cache/semantic_cache.py) 的 `clear_all`。
- **验证方式与结论**：源码确认；本次未执行真实评估，未测量业务污染规模或评估准确率变化。
- **建议方向（待讨论）**：独立评估依赖或命名空间，按运行 ID 区分记忆和结果；明确是否允许真实写工具及清理范围。
- **验收条件**：连续及并发评估不清理业务缓存、不写业务工单；每轮初始条件可控，结果能关联数据集版本和独立运行 ID。

<a id="arch-012"></a>

## ARCH-012：对话通信缺少完整的失败与取消语义

- **状态 / 优先级 / 关联 ADR**：Open / P2 / 无。
- **现象**：后端等待整个 Supervisor 与消息提交后才返回 SSE，并对完整答案分块模拟打字；前端 `chatStream` 不检查 HTTP 状态，直接取 body reader，未提供取消、显式错误事件或完成事件缺失的处理契约。
- **影响与优先级理由**：长任务期间无服务端进度，认证失败或流中断可能无法被可靠区分；多渠道、取消及恢复能力开发前应确定协议。完整审核后输出本身并非缺陷。
- **代码证据**：[chat.py](../../backend/app/api/chat.py) 的 `chat`、`event_stream`；[api.ts](../../frontend/src/api.ts) 的 `chatStream`；[Chat.tsx](../../frontend/src/pages/Chat.tsx) 的 `send`。
- **验证方式与结论**：源码确认；真实浏览器断网、代理超时及取消行为待验证，未测量首包延迟。
- **建议方向（待讨论）**：定义接受请求、进度、完成、失败与取消的契约，检查 HTTP 状态；若增加模型流式，仍须明确安全审核与可展示内容的边界。
- **验收条件**：401、服务异常、正常完成、无 done 中断及主动取消均有明确前后端行为；请求可关联到可查询的会话与结果。

<a id="arch-013"></a>

## ARCH-013：异步请求路径包含同步阻塞调用

- **状态 / 优先级 / 关联 ADR**：Open / P2 / 无。
- **现象**：`hybrid_search` 在异步函数中直接调用同步 `_ensure_bm25` 和 `store.search`；其下包含同步 Milvus 网络调用及索引构建。本地 BGE 已使用 to_thread，但这不覆盖向量存储和 BM25 路径。
- **影响与优先级理由**：慢查询或冷索引可能阻塞事件循环，影响其他请求；这是源码可见的阻塞风险，具体容量及耗时尚未测量，应在并发规模扩大前验证。
- **代码证据**：[retriever.py](../../backend/app/rag/retriever.py) 的 `hybrid_search`、`_ensure_bm25`；[store.py](../../backend/app/rag/store.py) 的 `get_client`、`all_chunks`、`search`；[local_bge.py](../../backend/app/llm/providers/local_bge.py) 的 `embed_query`、`rerank`。
- **验证方式与结论**：源码确认同步调用链；尚未执行事件循环延迟或吞吐测试，不能给出并发上限或性能提升比例。
- **建议方向（待讨论）**：隔离阻塞工作并控制排队、并发及请求预算，结合真实工作负载再确定资源配置。
- **验收条件**：模拟慢向量查询和冷索引时其他轻量请求仍可调度；超时、排队和取消可观察，真实容量以记录方法的测量结果为准。

<a id="arch-014"></a>

## ARCH-014：关键业务不变量缺少持久化回归测试

- **状态 / 优先级 / 关联 ADR**：Open / P1 / 无。
- **现象**：当前 tests 只有模型 provider 相关的 8 项测试；评估集关注生成结果，不能替代权限、状态一致性、幂等或并发的确定性测试。此前发现的 6 种异常仅做过一次性 mock。
- **影响与优先级理由**：模型接入测试通过仍无法拦住已确认的业务问题；修复核心边界前后需要可重复的保护。
- **代码证据**：[test_llm_providers.py](../../backend/tests/test_llm_providers.py) 的 8 个 `test_*`；[run_eval.py](../../backend/eval/run_eval.py) 的 `evaluate`；[pyproject.toml](../../backend/pyproject.toml) 的 pytest 配置。
- **验证方式与结论**：源码确认测试范围；同日单元测试 8 passed，mock 结果见下表。未测量代码覆盖率，不以测试数量推导可靠性。
- **建议方向（待讨论）**：针对 ARCH-001 至 ARCH-008 等问题补充可观察业务不变量的测试；纯逻辑使用 fake，事务、隔离和提交语义使用隔离数据库验证。
- **验收条件**：权限拒绝、重复请求、建单与接管一致性、无命中、缓存故障、人工竞争和知识发布失败均有可重复验证；测试不依赖生产数据或清理业务缓存。

## 六个 mock 场景的复核说明

这些是基准提交上的检查结果，不是新增测试文件。可在 backend 工作目录、tracelet 环境中，用 `unittest.mock.AsyncMock` / `patch` 重建下面的依赖替身；断言描述的是当前问题表现，修复后的回归测试应断言期望业务行为。

图级场景 M1–M3：调用 `supervisor.build_graph(ToolContext('t_test'), TraceCollector('t_test')).ainvoke(...)`；初始输入使用虚构 tenant/user/conversation ID。将 `session.get_history` 替换为一条非空历史以跳过首轮缓存；`get_entities` 返回空字典，`update_entities` 返回 `{'error_code': 'SIGN_INVALID'}`；`intent_router.classify` 返回指定 intent/route、confidence=1、相同实体、tokens=0；`ticket.create_from_context` 替换为返回 `TicketResult('tk_mock', 'created')` 的 AsyncMock。所有模型、外部数据及写入依赖均由对应场景的替身隔离。

| 场景 | 替身配置与操作 | 检查时的结果 / 可重复断言 | 关联问题 |
| --- | --- | --- | --- |
| M1 全部专业 Agent 失败 | intent=api_error，route=[api_diagnostic]；diagnose 抛 RuntimeError | final_answer 含“转接人工”，need_human 为 False，create_from_context 调用 0 次 | ARCH-002 |
| M2 诊断要求建单 | 同 M1 路由；diagnose 返回带非空规范 card、need_ticket=True 的 DiagResult | ticket_id 为 tk_mock，建单调用 1 次，need_human 仍为 False | ARCH-002 |
| M3 文档无命中 | intent=doc_qa，route=[doc_rag]；answer 返回 RagResult(answer=NO_HIT_MESSAGE, hit=False) | final_answer 不再等于 NO_HIT_MESSAGE，而为转人工提示；need_human=False，未建单 | ARCH-002 |
| M4 日志失败重放写工具 | 进程内注册无外部依赖的计数工具，使用默认 retries=1；_log_call 第一次抛异常、第二次成功；execute 一次 | 计数工具执行 2 次，execute 返回 ok=True；场景结束清除临时注册项 | ARCH-003 |
| M5 同租户会话复用 | fake db.execute 返回 tenant_id 与用户相同、user_id 不同的 Conversation 替身；调用 _get_or_create_conversation | 返回原会话对象，没有拒绝或创建新对象 | ARCH-001 |
| M6 完成答案后的 Redis 故障 | mock build_graph 返回 ainvoke 结果 {'final_answer': 'ready', 'intent': 'chitchat'}；session.append_message 抛 ConnectionError；调用 supervisor.run | ConnectionError 向调用方传播，已生成的答案未返回 | ARCH-004 |

动态验证没有覆盖其余待验证场景。后续实施应将有价值的断言转化为受维护的回归测试，而不是仅引用本次一次性检查。
