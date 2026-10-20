# 系统已知问题

本清单记录架构检查发现的问题、证据及后续验收条件，不代表已经批准修复方案。涉及系统长期架构选择时，按照 [ADR 规范](../adr/README.md)提出 Proposed；普通修复无需为了记录而创建 ADR。

2026-10-19 已形成用户要求实施的 [试用推进路线](pilot-roadmap.md)：先完成访问与数据边界，再补业务闭环和试用准入；ADR-0002、0003 已由用户明确接受并授权实施，正式状态以各项决定与验收记录为准。

2026-10-20 第二批已获用户明确接受 ADR-0004 至 0007 并授权实施；ARCH-002、003、004、005、009 已取得 [第二批隔离验收](pilot-roadmap.md#second-batch-verification)。后续第三批准入结果见下方。

2026-10-20 第三批按用户确认的“停止等待并保留查询、Chromium 自动化”完成：完整隔离后端 **272 passed**、前端 **26 passed**、真实依赖 Chromium **15 passed**，均零失败/错误/跳过。入口和证据解释见 [准入验收说明](pilot-acceptance.md)，实际执行结果见 [第三批记录](pilot-roadmap.md#third-batch-verification)。满足本轮合成数据、本机独立环境的准入条件，可进入受限小范围试用部署准备；本轮没有部署或开放真实用户。完整服务端取消仍延期，ARCH-012、014 保持 In Progress。

2026-10-20 下一阶段按用户选择先补系统演进能力，形成 [演进路线](evolution-roadmap.md)及 [ADR-0008（Proposed）](../adr/0008-versioned-knowledge-publication.md)、[ADR-0009（Proposed）](../adr/0009-bounded-retrieval-execution.md)：优先规划知识在线发布与回退、单轮固定版本及检索有界执行，随后推进独立评估、写接口与保留期治理、完整取消与容量治理。本轮为规划落盘及隔离原型实验，不部署；原问题状态保持不变，不能视为新方案已被接受、实现或验收。

2026-10-20 后续决定：用户明确接受 ADR-0008、ADR-0009 并要求开始执行，已登记 Accepted；ARCH-007、013 进入 In Progress。业务实现和当前验收进展见 [演进路线](evolution-roadmap.md)，不能把接受决策视为问题已经解决。

2026-10-20 首批演进验收：隔离后端 **302 passed**、前端 **26 passed**、真实依赖与 Chromium **16 passed**，均零失败/错误/跳过，专用资源清理成功。知识在线发布、回退、每轮固定版本及历史引用取得 [限定范围的完整证据](evolution-roadmap.md#真实依赖及-chromium-验收)，ARCH-007 为 Resolved；ARCH-013、014 继续 In Progress。未部署或迁移业务环境，后续阶段及容量/生命周期边界不变。

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
| [ARCH-001](#arch-001) | 会话权限规则不一致 | P1 | Resolved | [0002（Accepted）](../adr/0002-conversation-ticket-access.md) |
| [ARCH-002](#arch-002) | Agent 结果与转人工行为脱节 | P1 | Resolved | [0005（Accepted）](../adr/0005-agent-outcomes-and-handoff.md) |
| [ARCH-003](#arch-003) | 写工具重试可能重复执行 | P1 | Resolved | [0004（Accepted）](../adr/0004-durable-write-idempotency.md) |
| [ARCH-004](#arch-004) | 会话记忆与持久化记录缺少恢复边界 | P1 | Resolved | [0006（Accepted）](../adr/0006-conversation-source-and-recovery.md) |
| [ARCH-005](#arch-005) | 会话并发与人工接管缺少协调 | P1 | Resolved | [0007（Accepted）](../adr/0007-conversation-execution-fencing.md) |
| [ARCH-006](#arch-006) | 脱敏未覆盖输入、存储与全部出口 | P1 | Resolved | [0003（Accepted）](../adr/0003-sensitive-data-boundaries.md) |
| [ARCH-007](#arch-007) | 知识库发布与缓存版本不一致 | P1 | Resolved | [0008（Accepted）](../adr/0008-versioned-knowledge-publication.md) |
| [ARCH-008](#arch-008) | 初始化默认删表且缺少增量迁移 | P0 | Resolved | [0001（Accepted）](../adr/0001-managed-mysql-migrations.md) |
| [ARCH-009](#arch-009) | 演示时间假设进入运行逻辑 | P1 | Resolved | 无 |
| [ARCH-010](#arch-010) | 金额和单价使用浮点类型 | P2 | Deferred | 无 |
| [ARCH-011](#arch-011) | 评估与业务数据、缓存未隔离 | P1 | Deferred | 无 |
| [ARCH-012](#arch-012) | 对话通信缺少完整的失败与取消语义 | P2 | In Progress | 无 |
| [ARCH-013](#arch-013) | 异步请求路径包含同步阻塞调用 | P2 | In Progress | [0009（Accepted）](../adr/0009-bounded-retrieval-execution.md) |
| [ARCH-014](#arch-014) | 关键业务不变量缺少持久化回归测试 | P1 | In Progress | [0001（Accepted）](../adr/0001-managed-mysql-migrations.md)、[0002（Accepted）](../adr/0002-conversation-ticket-access.md)、[0003（Accepted）](../adr/0003-sensitive-data-boundaries.md)、[0004（Accepted）](../adr/0004-durable-write-idempotency.md)、[0005（Accepted）](../adr/0005-agent-outcomes-and-handoff.md)、[0006（Accepted）](../adr/0006-conversation-source-and-recovery.md)、[0007（Accepted）](../adr/0007-conversation-execution-fencing.md)、[0008（Accepted）](../adr/0008-versioned-knowledge-publication.md)、[0009（Accepted）](../adr/0009-bounded-retrieval-execution.md) |
| [ARCH-015](#arch-015) | 人工回复、直接追加及工作台更新尚未幂等 | P2 | Open | [0004（范围参照）](../adr/0004-durable-write-idempotency.md) |
| [ARCH-016](#arch-016) | 持久化操作及缓存去重记录缺少保留期治理 | P2 | Open | [0004](../adr/0004-durable-write-idempotency.md)、[0006](../adr/0006-conversation-source-and-recovery.md)、[0008（Accepted）](../adr/0008-versioned-knowledge-publication.md) |

<a id="arch-001"></a>

## ARCH-001：会话权限规则不一致

- **状态 / 优先级 / 关联 ADR**：Resolved / P1 / [ADR-0002（Accepted）](../adr/0002-conversation-ticket-access.md)。
- **现象**：会话列表对客户同时过滤 tenant_id 和 user_id，但详情、追加消息、聊天复用会话仅检查租户或内部身份；同租户其他客户的会话可被继续使用。
- **影响与优先级理由**：当前“自己的会话”列表语义与资源访问边界不一致，可能暴露同租户其他用户的消息或允许代发消息；增加入口前应统一。
- **代码证据**：[conversations.py](../../backend/app/api/conversations.py) 的 `list_conversations`、`get_conversation`、`add_customer_message`；[chat.py](../../backend/app/api/chat.py) 的 `_get_or_create_conversation`；[deps.py](../../backend/app/deps.py) 的 `assert_tenant_access`。
- **验证方式与结论**：源码确认；mock M5 已复现同租户、不同 user_id 的会话被复用。未执行真实 HTTP 越权测试。客户管理员是否需要租户范围访问仍需产品决定，不能把现有列表行为当成正式权限设计。
- **建议方向（待讨论）**：先确定角色与资源访问矩阵，再集中复用访问策略，覆盖查询、写入及工具入口。
- **验收条件**：权限矩阵明确；列表、详情、聊天、追加消息和反馈行为一致；同租户其他用户及跨租户访问按角色被允许或拒绝，并有回归证据。
- **2026-10-19 方案进展**：已按用户选择起草 ADR-0002，将工单 HTTP/工具读取及可信建单归属纳入同一权限矩阵；用户已明确回答“接受 ADR-0002、ADR-0003 并授权实施”；现已实现共享权限策略、可信工具身份、建单归属和反馈关联校验，并完成下述隔离验收。

- **2026-10-19 权限实施与验收**：基准为 `3da867361c79ef8fc6a0cbce3f7fbf78a7020ea8` 加本批未提交变更。[access.py](../../backend/app/access.py) 集中读取/写入与列表 SQL 条件，HTTP 和工单工具共用；客户管理员租户内只读、客户写入限本人、内部人员通过工作台处理他人会话；开发者不读取归属缺失、矛盾或关联孤立的工单。可信身份随 ToolContext 传递，单独 is_internal 标志不能提升角色。指定会话无效返回 404/403，不另建会话；反馈类型与消息关联验证在写入前完成。
- **2026-10-19 验收证据**：[test_pilot_boundaries_mysql.py](../../backend/tests/test_pilot_boundaries_mysql.py) 的前四项测试在随机独立 MySQL 中通过，涵盖两个租户、每租户两个开发者和一个客户管理员，以及 support/admin 的会话列表/详情、聊天、追加消息、反馈、工单 HTTP/工具与工作台矩阵；非法会话、反馈关联、伪造建单身份拒绝且无业务写入/模型调用，55 条他人记录不会挤掉本人列表结果。HTTP 使用 ASGI 和注入的合成身份、同步数据库适配器；未据此声称真实登录、浏览器或并发事务已经验收。前端读取服务端 can_write 隐藏他人会话的输入，类型与构建检查通过。完整套件 **171 passed，零失败/错误/跳过**，见 [第一批验收](pilot-roadmap.md#first-batch-verification)。ARCH-001 据此更新为 Resolved。

<a id="arch-002"></a>

## ARCH-002：Agent 结果与转人工行为脱节

- **状态 / 优先级 / 关联 ADR**：Resolved / P1 / [ADR-0005（Accepted）](../adr/0005-agent-outcomes-and-handoff.md)。
- **2026-10-20 起草时进展（后已获准并实施）**：按用户选择起草统一结果与集中决策提案；诊断建单可继续 AI，无命中先解释，全部技术失败尝试建单转人工。具体契约及事务方案保持 Proposed，尚未实施。
- **现象**：各 Agent 返回不同结果字段，Supervisor 用 `getattr` 解释；专业 Agent 全部失败时仍汇总出“转接人工”文案，却未建单或置 need_human。诊断 need_ticket 可以建单但不改变 need_human；RAG 无命中答案没有 card，汇总时被丢弃。
- **影响与优先级理由**：用户看到的承诺、工单和实际会话模式可能矛盾，后续扩展 Agent 或人工流程会继续继承这个歧义。
- **代码证据**：[supervisor.py](../../backend/app/agents/supervisor.py) 的 `build_graph` 内 `specialists_node`、`ticket_node`、`summarize_node`；[doc_rag.py](../../backend/app/agents/doc_rag.py) 的 `RagResult`、`answer`；[chat.py](../../backend/app/api/chat.py) 的 `chat`。
- **验证方式与结论**：源码确认；mock M1、M2、M3 已分别复现全失败、建单与模式分离、无命中解释丢失。是否允许“建单但继续 AI”应明确建模，而非隐含依赖两个布尔值。
- **建议方向（待讨论）**：统一 Agent 结果契约与失败分类，集中决策建单、人工模式及文案；只有动作成功才声称“已完成”。
- **验收条件**：全失败、部分失败、无命中、证据不足、建单失败及主动转人工均有确定结果；回复与真实动作一致；明确并验证建单与人工模式的关系。

- **2026-10-20 实施与验收**：统一 AgentOutcome 和集中决策已实现；无命中保留解释、部分失败保留有效结果，全部技术失败及明确人工需求形成待提交动作。建单与模式确认在最终事务完成，失败不声称成功。真实图结果矩阵及 SQL 建单/反馈回归通过。 实现基准为 `91b7209` 加本批工作区；完整隔离 **238 passed，零失败/错误/跳过**，前端 **15 passed** 及构建通过。范围和剩余限制见 [第二批验收](pilot-roadmap.md#second-batch-verification)，据此更新为 Resolved；不代表真实浏览器或外部模型已验收。

<a id="arch-003"></a>

## ARCH-003：写工具重试可能重复执行

- **状态 / 优先级 / 关联 ADR**：Resolved / P1 / [ADR-0004（Accepted）](../adr/0004-durable-write-idempotency.md)。
- **2026-10-20 起草时进展（后已获准并实施）**：已起草持久化操作、可信请求身份、同库事务及结果查询方案，保持 Proposed。独立普通修复已将 `_record_call` 放在业务重试块之外，日志异常只记录固定告警，不重放成功业务或覆盖失败结果；没有改变真实执行异常的重试预算，尚无持久化幂等或统一建单，因此不能关闭本问题。新增 [日志隔离回归](../../backend/tests/test_tool_log_isolation.py)；实际验收另行追加。
- **现象**：`execute` 把业务工具执行和 `_log_call` 放在同一重试块。写入成功后日志失败会重放工具；`create_ticket` 每次生成新 ID 并独立提交，缺少请求幂等约束。反馈转人工另有直接建单路径。
- **影响与优先级理由**：重试、重复提交或异常恢复可能产生重复工单；后续接入真实写操作前必须处理。
- **代码证据**：[registry.py](../../backend/app/tools/registry.py) 的 `ToolSpec`、`execute`；[ticket_tools.py](../../backend/app/tools/ticket_tools.py) 的 `create_ticket`；[tickets.py](../../backend/app/api/tickets.py) 的 `submit_feedback`。
- **验证方式与结论**：源码确认；mock M4 已验证成功写入后首次日志失败导致业务执行两次。真实数据库提交后连接中断的恢复行为待验证。
- **建议方向（待讨论）**：区分读写重试；日志失败不重放业务；为写操作设计持久化幂等键、唯一约束及结果查询，并统一建单入口。
- **验收条件**：同一业务请求重复提交、超时重试及日志失败均不会产生重复业务记录；能区分执行失败与结果未知，返回同一已提交结果。

- **2026-10-20 实施与验收**：聊天、反馈、独立建单工具共用可信持久化操作及唯一约束；日志不重放业务，写重试要求声明幂等能力，提交后异常从新连接查询。独立 SQL 验证首次会话重试、内容冲突、重复反馈、失败草稿重用及 commit 后抛错不产生重复工单。 实现基准为 `91b7209` 加本批工作区；完整隔离 **238 passed，零失败/错误/跳过**，前端 **15 passed** 及构建通过。范围和剩余限制见 [第二批验收](pilot-roadmap.md#second-batch-verification)，据此更新为 Resolved；不代表真实浏览器或外部模型已验收。

<a id="arch-004"></a>

## ARCH-004：会话记忆与持久化记录缺少恢复边界

- **状态 / 优先级 / 关联 ADR**：Resolved / P1 / [ADR-0006（Accepted）](../adr/0006-conversation-source-and-recovery.md)。
- **2026-10-20 起草时进展（后已获准并实施）**：已起草 SQL 事实、版本投影、统一消息与事务性附属任务方案，保持 Proposed；Redis 及附属故障的恢复能力尚未实施验收。
- **现象**：MySQL 保存消息，编排却只从 Redis 加载历史与实体，Redis 记忆 TTL 为 6 小时；人工及直接追加消息路径未更新 Redis。答案生成后的记忆、Trace 和成本写入串行等待，且位于图调用的异常兜底之外。
- **影响与优先级理由**：记忆过期后可能遗失仍在数据库中的上下文；答案已生成也可能因附属写入失败而无法返回。
- **代码证据**：[session.py](../../backend/app/memory/session.py) 的 `ENTITY_TTL`、`get_history`、`append_message`；[supervisor.py](../../backend/app/agents/supervisor.py) 的 `load_context`、`run`；[workbench.py](../../backend/app/api/workbench.py) 的 `human_reply`；[conversations.py](../../backend/app/api/conversations.py) 的 `add_customer_message`。
- **验证方式与结论**：源码确认；mock M6 已复现 Redis 历史写入失败阻止完成答案返回。TTL 到期后的真实恢复路径和断点恢复待验证。
- **建议方向（待讨论）**：明确会话事实来源及缓存重建路径；统一消息写入和上下文投影，区分必须提交的业务结果与可恢复的附属写入。
- **验收条件**：Redis 清空或过期后能恢复允许使用的历史；人工消息进入后续上下文；附属故障不丢失已提交结果，失败可追踪和恢复。

- **2026-10-20 实施与验收**：SQL 消息/实体/版本作为事实来源，Redis v2 投影可重建；客户、AI 与人工消息共用追加入口。附属任务与结果同事务登记，支持消费去重、租期重新领取及失败重试。真实 Redis TTL、清空、坏投影和原子缓存去重，以及 SQL 任务补偿/成本不重复用例通过。 实现基准为 `91b7209` 加本批工作区；完整隔离 **238 passed，零失败/错误/跳过**，前端 **15 passed** 及构建通过。范围和剩余限制见 [第二批验收](pilot-roadmap.md#second-batch-verification)，据此更新为 Resolved；不代表真实浏览器或外部模型已验收。

<a id="arch-005"></a>

## ARCH-005：会话并发与人工接管缺少协调

- **状态 / 优先级 / 关联 ADR**：Resolved / P1 / [ADR-0007（Accepted）](../adr/0007-conversation-execution-fencing.md)。
- **2026-10-20 起草时进展（后已获准并实施）**：用户选择每会话单轮、额外请求拒绝。数据库租期、代次、接管撤销资格及恢复接口已形成 Proposed，尚未实施或动态验证竞争。
- **现象**：聊天开始时读取 transferred_to_human，模型调用结束后直接写助手结果；未见提交时重新检查接管状态或会话版本。同会话无服务端轮次串行控制；Redis 实体更新是读、合并、写。
- **影响与优先级理由**：并发请求可能基于同一旧历史回答或覆盖实体；人工接管期间的在途 AI 仍可能写回。属于待验证的竞争风险，不能据此声称已经发生串话事故。
- **代码证据**：[chat.py](../../backend/app/api/chat.py) 的 `chat`；[workbench.py](../../backend/app/api/workbench.py) 的 `takeover_conversation`、`human_reply`；[session.py](../../backend/app/memory/session.py) 的 `update_entities`。
- **验证方式与结论**：源码确认缺少上述协调；并发竞争尚未动态复现。后续可用可控 barrier 暂停模型调用，插入第二轮或人工接管，再释放并检查写回。
- **建议方向（待讨论）**：明确会话轮次、请求幂等及接管状态迁移，用提交版本检查或合适的协调机制保证顺序；避免在模型调用期间持有长数据库事务。
- **验收条件**：多标签页并发、重复发送、实体同时更新和中途人工接管均有明确规则；过时 AI 结果不会违反已生效的接管决定。

- **2026-10-20 实施与验收**：同会话单轮占用、执行代次、数据库 UTC 租期与续租及最终版本检查已实现。独立异步 SQL 连接和 barrier 验证处理中同请求 202、新请求 409，不同会话独立占用，以及工作台接管、人工回复、主动转人工反馈均阻止在途 AI 写消息、实体和新工单；租期失效后原消息不重复。 实现基准为 `91b7209` 加本批工作区；完整隔离 **238 passed，零失败/错误/跳过**，前端 **15 passed** 及构建通过。范围和剩余限制见 [第二批验收](pilot-roadmap.md#second-batch-verification)，据此更新为 Resolved；不代表真实浏览器或外部模型已验收。

<a id="arch-006"></a>

## ARCH-006：脱敏未覆盖输入、存储与全部出口

- **状态 / 优先级 / 关联 ADR**：Resolved / P1 / [ADR-0003（Accepted）](../adr/0003-sensitive-data-boundaries.md)。
- **现象**：聊天保存 body.message 并直接交编排；Trace 保存输入、输出摘要时只截断。正常安全节点处理回复及 card，但澄清、缓存命中直接结束，工单提示在安全节点之后追加。
- **影响与优先级理由**：最终回复脱敏无法保护此前已存储或外发的内容；出口规则依赖分支约定。需在更多渠道和数据源接入前明确边界。
- **代码证据**：[chat.py](../../backend/app/api/chat.py) 的 `chat`；[trace.py](../../backend/app/observability/trace.py) 的 `TraceCollector.step`；[supervisor.py](../../backend/app/agents/supervisor.py) 的 `intent_node`、`security_node`、结束边与 `run`；[ticket.py](../../backend/app/agents/ticket.py) 的 `create_from_context`。
- **验证方式与结论**：源码确认调用顺序与保存路径。未做真实敏感数据外发测试，也未证明某个具体分支已发生泄漏；后续用合成敏感标记拦截存储和模型边界验证。
- **建议方向（待讨论）**：定义输入、模型上下文、业务证据、日志与输出各自允许的数据；统一最终出口并保留必要的非敏感诊断关联信息。
- **验收条件**：正常、澄清、缓存命中、建单失败、异常兜底及人工回复路径均验证敏感标记的存储、外发和展示策略；错误信息同样受控。
- **2026-10-19 方案进展**：已起草 ADR-0003，推荐仅保留脱敏内容、保留必要且已授权的业务 ID，覆盖入口、模型、存储和最终出口。用户已明确接受并授权实施；现已实现入口、模型门面、记忆/缓存、工单、日志与统一出口处理，并完成下述限定范围的验收。

- **2026-10-19 脱敏实施**：同一实现基准下，[desensitize.py](../../backend/app/guardrail/desensitize.py) 完整替换已识别敏感值，覆盖短凭证、结构化敏感字段、数字值、JSON/Unicode 转义及重复处理，合法业务 ID 不因长十六进制片段而误遮盖；字段名不是豁免。入口、模型门面、Redis 记忆/路由/答案缓存、知识入库/展示、工单、Trace、工具/审计日志及人工回复使用同一能力。Supervisor 的 `_finalize_result` 处理正常、缓存、澄清、工单提示及管线兜底；HTTP 验证错误和异常不回显原文，日志省略原始 traceback。
- **2026-10-19 验收证据与边界**：[test_pilot_privacy.py](../../backend/tests/test_pilot_privacy.py) 的 **36 项**替身测试覆盖模型 chat/stream、embedding、rerank、跨 chunk 凭证、Redis、真实编排图各出口、日志与错误处理；MySQL 测试另外验证真实消息/工单/Trace/工具日志/审计字段，以及输入处理失败无写入。完整隔离套件 **171 passed，零失败/错误/跳过**，见 [第一批验收](pilot-roadmap.md#first-batch-verification)。本状态仅针对 ADR-0003 定义的数据类型和处理边界，检测并不保证识别任意秘密；Redis、向量和模型使用替身，真实服务链路留待第三批；未扫描或改写旧业务数据，试用仍要求全新独立环境。ARCH-006 在上述边界内更新为 Resolved。

- **2026-10-20 第三批补验与误判修复**：真实 BGE 分数的小数尾数会误命中银行卡规则，导致分数和归一化缓存向量变成字符串。现保留 `[-1, 1]` 内有限 float 的数值类型，显式敏感字段、大数及自由文本仍按原规则脱敏；新增 [数值及敏感反例回归](../../backend/tests/test_privacy_numeric_vectors.py) 11 项。真实模型代理未观察到合成秘密标记；独立 SQL 的 21 张业务表、Redis 的 4 个键及 Milvus 的 66 个切片扫描均零命中，见 [第三批记录](pilot-roadmap.md#third-batch-verification)。仅证明这些合成标记和已约定边界，不保证任意敏感信息均可识别；不修改旧业务数据。

<a id="arch-007"></a>

## ARCH-007：知识库发布与缓存版本不一致

- **状态 / 优先级 / 关联 ADR**：Resolved / P1 / [ADR-0008（Accepted）](../adr/0008-versioned-knowledge-publication.md)。
- **现象**：全量 ingest 预热 embedding 后删除旧 collection，再逐篇生成及写入，最后登记 MySQL 元信息。BM25 是常驻进程缓存，`reset_bm25` 未见调用；语义缓存键只有 embedding 指纹与租户，没有知识库版本或过期设置；文档中心直接读取 Markdown。
- **影响与优先级理由**：中途失败可能留下部分索引；文档、向量、关键词索引和答案缓存可能引用不同版本。BM25 数据读取还默认限于 2000 条，不能保证扩容后完整。
- **代码证据**：[ingest.py](../../backend/app/rag/ingest.py) 的 `ingest_all`；[store.py](../../backend/app/rag/store.py) 的 `ensure_collection`、`all_chunks`；[retriever.py](../../backend/app/rag/retriever.py) 的 `_ensure_bm25`、`reset_bm25`；[semantic_cache.py](../../backend/app/cache/semantic_cache.py) 的 `key_for`、`put`；[docs.py](../../backend/app/api/docs.py) 的 `get_doc`。
- **验证方式与结论**：源码确认；真实中途失败、跨进程缓存失效及超过 2000 条的召回表现待验证，不记录虚构命中率。
- **建议方向（待讨论）**：构建、校验新知识版本后再发布；统一向量、关键词、文档展示、引用与答案缓存的版本，支持失败保留旧版和回退。
- **验收条件**：构建失败不损坏生效知识；发布后所有 worker 及缓存版本一致；引用可定位对应版本；大于默认条数时关键词索引仍完整。
- **2026-10-19 延期决定依据与重评**：用户要求实施的 [试用路线](pilot-roadmap.md) 限定固定知识，更新采用停服、重建与恢复验收，在线版本发布安排在试用后。需要不停服更新、动态跨 worker 切换或超过当前索引读取边界时须先重评；该限制未修复现有重建失败风险，不标记 Resolved。
- **2026-10-20 重评与提案**：用户选择先补演进能力、首批知识在线发布，已起草 ADR-0008，推荐不可变 SQL 文档/切片快照、独立 Milvus 集合、生效指针短事务、原操作/缓存任务固定版本和可定位历史引用。CLI 发布与回退、故障及双进程/2001 切片验收已拆入 [首批任务](evolution-roadmap.md#第一阶段实施任务)。本轮仅规划，状态保持 Deferred；提案获准且完成实现/验收前继续按现有维护窗口操作。
- **新提案的验收解释**：所有 worker 的新接收轮次应使用同一生效指针；已绑定旧版的在途请求和任务允许按旧版完成，缓存以版本隔离。验收须断言每轮一致及新请求切换，不能要求发布时抹掉仍被使用的历史版本。
- **2026-10-20 实施与验收**：已实现不可变 SQL 快照/独占集合、完整校验、SQL CAS 发布与回退、固定操作版本及缓存命名空间、历史文档与引用、运维 CLI；新增 0003 revision，不改旧迁移。隔离后端 302、前端 26、完整真实依赖 Chromium 16 项通过并清理；包含真实子进程提交前后退出、双进程在线切版/回退、原 UUID 重放、真实 Redis 迟到任务和 2001 切片完整性。具体代码、报告、故障修正及证据边界见 [首批实施记录](evolution-roadmap.md#2026-10-20-首批实施记录)。本问题在该范围登记 Resolved；首次业务接入仍需维护窗口，历史保留与容量由 ARCH-016 后续治理。

<a id="arch-008"></a>

## ARCH-008：初始化默认删表且缺少增量迁移

- **状态 / 优先级 / 关联 ADR**：Resolved / P0 / [ADR-0001（Accepted）](../adr/0001-managed-mysql-migrations.md)。
- **基准现象（2026-10-18）**：Makefile 的 init-db 固定传 `--recreate`，setup 依赖 init-db；该分支执行 `Base.metadata.drop_all`。不传参数时仅 create_all，仓库未见业务 schema 的增量迁移流程。
- **影响与优先级理由**：在有业务数据的库上重复 setup 会删表；继续增加字段后缺乏保留已有数据的演进路径，应最先消除直接数据风险。
- **代码证据**：[Makefile](../../Makefile) 的 `init-db`、`setup`；[init_db.py](../../backend/scripts/init_db.py) 的 `main`；[pyproject.toml](../../backend/pyproject.toml) 的依赖及工具配置。
- **验证方式与结论**：源码确认删表路径；为避免破坏现有数据，本次没有执行 setup、删表或迁移。未来只在一次性测试库验证。
- **建议方向（待讨论）**：分开安全初始化、增量迁移、种子数据与显式重置；为 schema 演进选择合适的迁移机制。
- **验收条件**：对有数据的测试库重复安全初始化不丢记录；已有 schema 能按受管理的版本迁移；破坏性重置有独立且明确的入口和说明。

### 2026-10-19 首批修复：默认初始化入口安全化

- **检查与实现基准**：`a7b31fa49401590be79a7755773cbd49d727809e` 加本批未提交变更；保留工作区原有基础设施、依赖、README 和 Supervisor 修改。本批不改变 schema 演进机制，不新建 ADR。
- **补充源码证据**：[seed_data.py](../../backend/scripts/seed_data.py) 的 `_clear` 会清空 ApiCallLog、ApiKey、App、ApiEndpoint、ErrorCode、Invoice、UsageRecord、User、Tenant、Plan 十类表；[ingest_knowledge.py](../../backend/scripts/ingest_knowledge.py) 的 `main` 固定调用 `ingest_all(recreate=True)`。仅去掉初始化的 `--recreate` 仍不能保证旧版 setup 保留数据。
- **已实现**：[Makefile](../../Makefile) 的 `init-db`、`setup` 只创建缺失表和集合，不再自动执行 seed/ingest。[init_db.py](../../backend/scripts/init_db.py) 的 `main` 要求同时指定 `--recreate --yes` 才能重建，未确认时在加载存储依赖之前拒绝。新增 `reset-db` 和 `setup-demo` 均先检查 `CONFIRM_RESET=1`，没有执行数据操作的前置依赖；[setup_demo.py](../../backend/scripts/setup_demo.py) 的 `main` 先确认，再依次启动初始化、种子和知识脚本，失败即停止。
- **行为边界**：直接 seed/ingest 仍保留覆盖行为，README 已明确说明。演示装载和跨存储重置不具有整体事务或自动回滚保证；默认初始化不会修改已有表结构。增量迁移仍待下一批设计和实施，因此不标记 Resolved。
- **回归证据**：[test_data_setup.py](../../backend/tests/test_data_setup.py) 的 11 项测试通过，覆盖已有集合连续初始化不删不重建、缺失集合仅创建一次、拒绝未确认操作、重置顺序、存储异常和演示步骤失败即停。全部使用存储或子进程替身，不代表真实 MySQL/Milvus 验证。
- **实际运行结果**：在 tracelet conda 环境、backend 目录关闭无关插件并显式加载 pytest_asyncio，运行本批三个测试文件及既有 `test_model_for_uses_provider_model_mapping`、`test_semantic_cache_key_contains_embedding_fingerprint`、`test_unknown_provider_reports_available_names`，结果 **14 passed、12 skipped**；本批 Python 文件的 Ruff 检查通过。存在 PyMilvus 的 pkg_resources 弃用警告，不据此推导业务故障。
- **未完成的验证**：[test_make_data_setup.py](../../backend/tests/test_make_data_setup.py) 的 11 项真实 GNU Make 测试因本机缺少 GNU Make 跳过；[test_init_db_mysql.py](../../backend/tests/test_init_db_mysql.py) 因未提供专用管理连接跳过，尚不能宣称真实数据库保留记录验收通过。该测试通过 `TRACELET_TEST_MYSQL_ADMIN_URL` 显式启用，仅在随机生成的独立库中建表、写入和比较完整记录及关联，结束后清理该库；Milvus 仍使用替身。
- **完整测试环境限制**：既有异步测试停在 Windows asyncio 创建本机 socketpair 的步骤，已中止，未取得本批完整测试结果。两次提权测试请求均因自动审批服务模型容量不足而未执行，不是测试断言失败。Docker API 同样受当前环境访问限制；未对配置库运行重置、种子装载或知识重建。

### 2026-10-19 第二批：独立初始化验收入口

- **实现范围与基准**：仍以 `a7b31fa49401590be79a7755773cbd49d727809e` 加未提交工作区为基准，保留首批及用户已有改动。本批增加测试入口和测试，不改变业务架构或增量迁移策略。
- **入口与隔离**：[verify_setup.py](../../backend/scripts/verify_setup.py) 的 `main` 同时供 `make verify-setup` 和 PowerShell 调用，显式指定 [compose.verify.yml](../../compose.verify.yml) 及每次随机生成的项目名，清除宿主 Compose 配置来源变量并关闭默认 env-file 加载。独立项目只有测试服务和临时 MySQL；MySQL 健康后才运行完整后端测试。MySQL 使用 tmpfs，运行时为内部网络，没有宿主端口、业务资源复用、宿主目录或 Docker socket 挂载。
- **构建边界**：[Dockerfile.verify](../../Dockerfile.verify) 使用 Python 3.12、GNU Make 和现有 `[dev]` 依赖，不安装本地模型依赖；[专用构建白名单](../../Dockerfile.verify.dockerignore) 排除真实配置、凭证、模型及缓存。镜像构建及完整依赖安装目前尚未取得实际成功证据。
- **成功与清理契约**：必须同时取得零执行退出码及有效、非空、零失败/错误/跳过的 JUnit 报告，统计须与测试记录一致。清理前将报告复制到已忽略的 `.pytest_cache/verify/<项目名>/junit.xml`；`finally` 只清理该配置和项目的容器、网络和卷。中断或清理失败返回非零，清理失败打印作用域相同的恢复命令，不会输出“验收通过”。
- **替身回归证据**：[test_verify_setup.py](../../backend/tests/test_verify_setup.py) 的 30 项编排与报告测试通过，覆盖成功、唯一项目/报告、不受业务 Compose 环境变量影响、依赖或测试失败、报告复制/缺失/解析/统计/跳过问题、进程异常、中断以及清理失败。所有 Docker 子进程使用替身，不能证明真实引擎生命周期。[Make 测试](../../backend/tests/test_make_data_setup.py) 新增 verify-setup 到共享脚本的入口断言，待 GNU Make 环境实际执行。
- **本机实际检查**：tracelet conda 环境（Python 3.12.15），关闭无关插件、显式加载 pytest_asyncio，并将 pytest 临时目录放在已忽略的缓存中；运行四个初始化/编排测试文件及既有三项同步 provider 测试，结果 **44 passed、13 skipped**。跳过为 12 项 GNU Make 测试和 1 项真实 MySQL 测试；既有异步测试本轮未取得完整执行结果。本批及首批相关 Python 文件 Ruff 检查通过；`docker compose --file compose.verify.yml config --quiet` 成功，并检查解析后的 JSON：仅 mysql、tests 两个服务、无端口/固定容器名/volume 挂载，MySQL 为 tmpfs，默认网络为 internal，tests 依赖 MySQL 健康。另对一项编排单元测试执行真实 pytest JUnit 输出，结果 1 passed，`validate_report` 成功读取并返回 1；该 smoke 不增加独立测试数量，也不代表容器验收。PyMilvus 的 pkg_resources 弃用警告仍存在。
- **隔离入口实际尝试**：在 backend 目录执行 `python -B -m scripts.verify_setup`，本次项目为 `tracelet-verify-1492c85b6dba4b7c9aa1afac61b15852`；Docker API 访问被拒绝，入口退出码为 1，未取得 JUnit 报告，并明确输出清理失败及限定本次项目的恢复命令。随后提权请求因自动审批服务模型容量不足未执行。尚无真实容器、MySQL 保留记录或完整零跳过验收通过证据，不能把配置校验或替身测试当作替代。
- **遗留状态**：ARCH-008 保持 In Progress。后续先在允许访问 Docker 且可下载依赖的环境运行该入口，取得完整验收证据；增量迁移仍属下一批任务。未对业务配置库执行初始化、重置、种子装载或知识重建。

### 2026-10-19 增量迁移前置调查与提案

- **已完成调查**：检查基准仍为 `a7b31fa49401590be79a7755773cbd49d727809e` 加当前未提交工作区。当前 ORM 共 19 张受管表，逐表离线编译 MySQL CREATE TABLE 全部成功，`message.created_at` 编译为 DATETIME(6)；仓库及 tracelet 环境尚未配置/安装 Alembic。离线编译不是数据库迁移测试。
- **方案准备**：新增 [ADR-0001](../adr/0001-managed-mysql-migrations.md)，状态 Proposed；推荐固定初始迁移、已有库校验后显式接管、初始化复用受控升级，并明确版本表、重置、管理操作互斥、非事务性 DDL 和验收边界。尚未修改业务迁移机制，也未登记为 Accepted。
- **前置验收限制**：独立 Compose 配置复核通过。根据本次继续推进指令再次请求运行隔离验收，但自动审批服务因模型容量不足未执行；没有新增真实 MySQL/完整测试通过证据。前两批替身结果保持原记录，不能当作迁移验收。
- **待决定事项**：依项目 ADR 规范，用户须对上述具体提案明确决定后，方能实施推荐的重大架构方案及更新正式状态；此前的继续工作授权未对应这份尚不存在的提案版本。环境允许后仍须补齐真实库验证，ARCH-008 保持 In Progress。

### 2026-10-19 第三批：受管迁移与旧库接管实现

- **批准依据**：用户在本聊天针对 ADR-0001 明确回答“接受 ADR-0001 并实施”，已据此更新 ADR 为 Accepted。上节 Proposed 是提案形成时的历史状态；本节实施同一已获准方案，不以 AI 自行决定替代用户批准。基准仍为 `a7b31fa49401590be79a7755773cbd49d727809e` 加未提交工作区。
- **固定历史**：新增 Alembic 依赖及迁移环境；[初始 revision](../../backend/migrations/versions/0001_initial_schema.py) 使用 [冻结定义](../../backend/migrations/baseline.py) 建立 19 张业务表，不调用当前 ORM 的 create_all。保留现有字段类型及 DATETIME(6)，不夹带业务变更；版本由 alembic_version 管理。
- **受控入口**：[migrate_db.py](../../backend/scripts/migrate_db.py) 提供 current、check、upgrade、adopt-baseline --yes。[schema_migrations.py](../../backend/app/schema_migrations.py) 只允许空库或已知单链版本升级；非空未登记库拒绝升级，须校验冻结基线后显式接管。重复接管不再次 stamp，head 升级只校验；未知版本、分支历史及不兼容结构返回非零，不自动修补。MySQL 写命令复用同一连接并持有按数据库名区分的零等待 advisory lock。
- **结构边界**：[schema_compare.py](../../backend/app/schema_compare.py) 比较表/列、类型和精度、默认值、nullable、自增、主键、唯一约束、索引和外键，按语义处理名称、整数显示宽度、Boolean/TINYINT(1) 及外键支持索引。视图、触发器和存储例程检测到即拒绝；检查依赖账号对 schema 的可见性，不承诺覆盖所有 MySQL 对象或任意定制库。
- **初始化兼容变化**：init-db/setup 现改为受控 MySQL 升级后确保 Milvus 集合存在；旧的未登记非空库将安全拒绝，需要先检查/接管。显式重置会清理受管业务表和版本表，再通过迁移历史重建；有未受管对象/表或未知版本则拒绝。MySQL 失败时不会继续操作 Milvus；跨存储重置不承诺事务原子性。README 已补安装依赖、接管、升级、迁移开发及恢复说明。
- **隔离验收更新**：测试镜像及专用构建白名单已包含 alembic.ini 和迁移历史。真实库测试复用 [mysql_engine fixture](../../backend/tests/conftest.py)，只使用显式测试管理连接创建的随机独立库，并检查实际选中的数据库名；不使用应用配置库。
- **实际通过证据**：tracelet 的 Python 3.12.15/SQLAlchemy 2.1.5 环境中，使用本机 rag 环境已有 Alembic 1.20.0、Mako 1.4.3 的副本，仅放入已忽略的 `.pytest_cache/migration-deps` 并通过 PYTHONPATH 临时加载，未修改两个 conda 环境。运行现有可执行同步测试和本批测试，结果 **79 passed、25 skipped**；其中 [迁移单元测试](../../backend/tests/test_schema_migrations.py) 34 项通过。真实 Alembic 离线生成 19 张业务表加版本表的 MySQL SQL，包含 DATETIME(6)、没有 DROP TABLE；冻结结构与当前 ORM 比较无差异。相关 Python 文件 Ruff、Compose 配置及差异检查通过。这些证据不证明真实 MySQL 迁移成功。
- **待实测项目**：25 项跳过为 12 项 GNU Make 入口测试及 13 项真实 MySQL 测试（含 [新增的 12 项迁移场景](../../backend/tests/test_schema_migrations_mysql.py)）。真实场景覆盖旧库接管、字段/索引漂移拒绝、未知版本、只读命令、显式重置、管理锁和临时第二条 revision 的成功/中途失败；测试字段只存在临时迁移目录，不进入生产历史。既有 5 项异步 provider 测试本轮未取得执行结果。
- **仍未完成的前置验收**：本轮 Docker 隔离验收和临时目录包源安装的提权请求均因自动审批服务模型容量不足未执行；沙箱内 Docker API/包源代理仍不可访问。已采用本机现有依赖完成离线及替身验证，但不能替代真实 MySQL、Docker 生命周期及完整零跳过验收。未对当前业务库执行迁移、接管、重置或种子装载；ARCH-008 保持 In Progress，待真实验证满足完整验收条件后再标记 Resolved。

### 2026-10-19 完整隔离验收完成

- **状态依据**：已完成上述受管迁移实现及下列真实 MySQL/完整套件验收，ARCH-008 更新为 Resolved。仍以 `a7b31fa49401590be79a7755773cbd49d727809e` 加当前未提交变更为实现基准。前述审批、网络及部分测试记录保留为历史；本状态不表示已经接管或迁移用户当前业务库。
- **环境前置完成**：本次提权验收得到执行。Docker 首次因 Python 镜像的 Docker Hub 认证连接超时而构建失败，随后通过主机已有代理从官方 registry 预取 linux/amd64 镜像，校验清单、配置和每层 SHA-256 后导入本机缓存；没有修改 Docker 全局代理设置，一次性下载包已删除。tracelet 开发环境已成功执行 `python -B -m pip install -e ".[dev]"`，正式安装 Alembic 1.20.0、Mako 1.4.3 和当前后端，不再依赖临时副本。
- **全新安装发现并修复的兼容问题**：首次实际套件为 102 passed、1 failed、6 errors，失败均来自 PyMilvus 2.4.15 导入已被 Setuptools 84 移除的 pkg_resources。已在 [pyproject.toml](../../backend/pyproject.toml) 增加运行依赖 `setuptools>69,<82`；移除发生在 Setuptools 82，参见 [官方变更记录](https://github.com/pypa/setuptools/blob/main/NEWS.rst)（2026-10-19 经 Context7 核查）。这不改变 Milvus/SDK 版本或业务字段。
- **最终执行方法与环境**：在 backend 目录执行 `python -B -m scripts.verify_setup`，项目为 `tracelet-verify-85e92d7172014f2187da1b0ef95c1805`。Python 3.12.15、MySQL 8.0.46、SQLAlchemy 2.1.5、Alembic 1.20.0、PyMilvus 2.4.15、Setuptools 81.0.0、pytest 9.1.1；从当前 `[dev]` 依赖构建镜像，不安装本地模型。MySQL 使用独立内部网络和 tmpfs，测试只创建和删除随机独立测试库。
- **最终结果**：**109 passed，0 failed，0 errors，0 skipped**，编排入口退出码 0。包括 13 项真实 MySQL 初始化/迁移场景、12 项真实 GNU Make 入口测试及既有 8 项 provider 测试。接管前拒绝普通升级、校验后仅登记版本、保留完整业务记录及关联、重复升级、结构漂移/未知版本拒绝、只读命令、重置版本表、管理锁、临时增量 revision 和非事务性 DDL 失败均取得实际断言证据；向量、Redis、模型和演示子进程仍使用替身，不据此声称真实 Milvus 或外部模型通过验证。
- **报告与清理证据**：报告保存在本机已忽略的 `.pytest_cache/verify/tracelet-verify-85e92d7172014f2187da1b0ef95c1805/junit.xml`，再次通过 validate_report 检查为 109 项、零失败/错误/跳过。编排 finally 完成清理后返回成功；另按 Compose 项目标签查询，容器、网络和卷均无遗留。镜像内 `python -m pip check` 返回 No broken requirements found。相关 Ruff、Compose 配置、文档链接及 Git 差异检查通过，报告/依赖缓存未纳入版本管理。
- **证据边界**：保留 23 条弃用警告，来自 SDK 的 pkg_resources 及模型的 datetime.utcnow；不影响本次通过判定，也未掩盖它们。未测量性能或锁表时间，没有自动重试失败 DDL、修改业务库或运行演示装载；旧库须依 README 先在副本核查，再显式接管。

<a id="arch-009"></a>

## ARCH-009：演示时间假设进入运行逻辑

- **状态 / 优先级 / 关联 ADR**：Resolved / P1 / 无。
- **现象**：Key 过期判断固定使用 2026-06-15；两处建单 ID 含同一固定日期。“近 N 分钟”统计以最近 429 或日志为锚点，只设置下界，没有锚点上界。
- **影响与优先级理由**：接入真实数据后，“当前过期”和“最近调用”可能与用户理解不一致，统计也可能包含锚点之后的其他数据；上线真实诊断前需分离演示与运行语义。
- **代码证据**：[apikey.py](../../backend/app/tools/apikey.py) 的 `query_apikey_status`；[logs.py](../../backend/app/tools/logs.py) 的 `query_recent_call_stats`；[ticket_tools.py](../../backend/app/tools/ticket_tools.py) 的 `create_ticket`；[tickets.py](../../backend/app/api/tickets.py) 的 `submit_feedback`。
- **验证方式与结论**：源码确认固定日期及查询范围；真实时间窗口诊断待验证。种子脚本固定数据时间本身不是缺陷。
- **建议方向（待讨论）**：运行时间与演示时钟分开；明确实时窗口和历史事件窗口，统一时区、上下界与展示含义。
- **验收条件**：过期边界、跨日与时区、实时和历史窗口均可确定性验证；演示数据可复现且不改变正常运行的时钟语义。

- **2026-10-20 实施与验收**：运行路径统一真实 UTC，无时区 DATETIME 按既有 UTC 约定解释；到期瞬间判过期，工单使用创建时 UTC 日期。统计默认当前窗口，显式本租户调用事件才用历史窗口 `(start, end]`，零调用保留为有效证据。共享建单服务、时间窗口及输入边界的 SQLite/独立 MySQL 回归通过，完整 **238 passed**；详见 [第二批验收](pilot-roadmap.md#second-batch-verification)。此为普通修复，不新增 ADR，据此更新为 Resolved。

<a id="arch-010"></a>

## ARCH-010：金额和单价使用浮点类型

- **状态 / 优先级 / 关联 ADR**：Deferred / P2 / 无。
- **现象**：`Plan.price_per_call`、`overage_price_per_call` 和 `Invoice.amount` 使用 `Mapped[float]`，账单工具直接返回这些值。
- **影响与优先级理由**：后续若承担金额计算或对账，需要明确精度及舍入，避免依赖二进制浮点的表示；本次没有发现或复现具体错误账单，因此列为真实计费能力完善前的风险。
- **代码证据**：[models.py](../../backend/app/models.py) 的 `Plan`、`Invoice`；[billing_tools.py](../../backend/app/tools/billing_tools.py) 的 `query_plan`、`query_bill`。
- **验证方式与结论**：源码确认字段类型；实际金额累计误差和数据迁移表现待验证，不声称现有账单已计算错误。
- **建议方向（待讨论）**：评估定点存储、货币与单价精度、舍入及序列化约定，并与实际计费来源对齐。
- **验收条件**：精度和舍入规则明确；边界金额、单价乘调用量、迁移前后对账及接口往返验证通过。
- **2026-10-19 延期决定依据与重评**：用户要求实施的 [试用路线](pilot-roadmap.md) 限定账单只读，不承担真实金额计算。真实扣费、单价乘调用量或对账启用前必须完成精度与迁移验证；本批不改金额字段。

<a id="arch-011"></a>

## ARCH-011：评估与业务数据、缓存未隔离

- **状态 / 优先级 / 关联 ADR**：Deferred / P1 / 无。
- **现象**：`evaluate` 清理所有语义缓存及 routecache，再使用数据集租户调用真实 Supervisor；固定 eval 会话 ID 可在同一 Redis 中复用，链路可能建单并记录记忆、Trace 和成本。
- **影响与优先级理由**：与业务共用依赖时会影响缓存和业务记录；重复评估可能带入上一轮记忆，破坏评估条件。应在继续依赖评估结果前隔离。
- **代码证据**：[run_eval.py](../../backend/eval/run_eval.py) 的 `evaluate`；[eval.py](../../backend/app/api/eval.py) 的 `run_eval`；[supervisor.py](../../backend/app/agents/supervisor.py) 的 `run`；[semantic_cache.py](../../backend/app/cache/semantic_cache.py) 的 `clear_all`。
- **验证方式与结论**：源码确认；本次未执行真实评估，未测量业务污染规模或评估准确率变化。
- **建议方向（待讨论）**：独立评估依赖或命名空间，按运行 ID 区分记忆和结果；明确是否允许真实写工具及清理范围。
- **验收条件**：连续及并发评估不清理业务缓存、不写业务工单；每轮初始条件可控，结果能关联数据集版本和独立运行 ID。
- **2026-10-19 延期决定依据与临时防护**：用户要求实施的 [试用路线](pilot-roadmap.md) 将完整评估隔离安排在恢复评估能力之前。当前工作区在 [offline_runs.py](../../backend/app/offline_runs.py) 集中限制执行环境：仅 dev/test/eval 允许，pilot、production 及未知环境拒绝；HTTP、evaluate 函数、评估 CLI 和压测入口在业务依赖加载与副作用前检查。指标查询保留，并返回能力信息供页面隐藏评估入口。允许执行的环境仍可能共用依赖，必须使用独立数据；未完成运行 ID 和独立依赖设计，因此保持 Deferred，不能视为 Resolved。20 项离线任务保护测试已通过，包含拒绝前不加载业务依赖、CLI 非零退出、HTTP 拒绝及只读指标能力；完整结果见 [第一批验收](pilot-roadmap.md#first-batch-verification)。
- **2026-10-20 下一阶段顺序**：[演进路线](evolution-roadmap.md#阶段与依赖)将完整隔离列为知识版本治理之后的第二阶段，固定知识/数据集/模型配置并隔离每轮依赖与运行 ID；当前执行限制继续有效，本轮不恢复评估入口。

<a id="arch-012"></a>

## ARCH-012：对话通信缺少完整的失败与取消语义

- **状态 / 优先级 / 关联 ADR**：In Progress / P2 / 无。
- **现象**：后端等待整个 Supervisor 与消息提交后才返回 SSE，并对完整答案分块模拟打字；前端 `chatStream` 不检查 HTTP 状态，直接取 body reader，未提供取消、显式错误事件或完成事件缺失的处理契约。
- **影响与优先级理由**：长任务期间无服务端进度，认证失败或流中断可能无法被可靠区分；多渠道、取消及恢复能力开发前应确定协议。完整审核后输出本身并非缺陷。
- **代码证据**：[chat.py](../../backend/app/api/chat.py) 的 `chat`、`event_stream`；[api.ts](../../frontend/src/api.ts) 的 `chatStream`；[Chat.tsx](../../frontend/src/pages/Chat.tsx) 的 `send`。
- **验证方式与结论**：源码确认；真实浏览器断网、代理超时及取消行为待验证，未测量首包延迟。
- **建议方向（待讨论）**：定义接受请求、进度、完成、失败与取消的契约，检查 HTTP 状态；若增加模型流式，仍须明确安全审核与可展示内容的边界。
- **验收条件**：401、服务异常、正常完成、无 done 中断及主动取消均有明确前后端行为；请求可关联到可查询的会话与结果。
- **2026-10-19 最小修复进展**：[chatStream.ts](../../frontend/src/chatStream.ts) 在读取 body 前检查 HTTP 状态，对 401/403/404/服务异常给出固定错误；聊天页退出等待并展示错误，reader 在结束或异常时释放。保留既有成功接口和事件格式。本批不增加取消、无 done 中断、进度或恢复契约，问题继续处理中。前端 7 项通信测试、类型检查和生产构建通过；这些是函数级替身验证，不是浏览器断网或完整取消契约的证明。

- **2026-10-20 通信补充**：前端识别 202/409，缺少 done 的中断进入待确认状态；保持原 UUID 查询/重试，刷新不保存输入正文。函数级通信和本地状态共 15 项测试及构建通过；仍未做真实浏览器断网、取消及代理故障，保持 In Progress。

- **2026-10-20 第三批实现**：聊天、反馈和操作查询接受可选 AbortSignal；停止等待及页面卸载终止本地请求，执行代次屏蔽迟到回调。查询认证失效提示重新登录并保留 UUID；重试后撤回旧的 can_retry，须再次查询确认。SSE 按完整事件处理 UTF-8、CRLF、多行 data、注释与完成事件，done 后结束读取并释放资源。服务端接口、事实来源、提交与接管契约不变；完整取消仍未实现，状态继续 In Progress。实际浏览器与真实依赖证据见 [第三批记录](pilot-roadmap.md#third-batch-verification)。
- **2026-10-20 下一阶段顺序**：完整服务端取消及提交竞争列入 [演进路线第四阶段](evolution-roadmap.md#阶段与依赖)。首批 ADR-0009 只治理检索等待和实际线程容量，不增加业务取消契约，不据此关闭本问题。

<a id="arch-013"></a>

## ARCH-013：异步请求路径包含同步阻塞调用

- **状态 / 优先级 / 关联 ADR**：In Progress / P2 / [ADR-0009（Accepted）](../adr/0009-bounded-retrieval-execution.md)。
- **现象**：`hybrid_search` 在异步函数中直接调用同步 `_ensure_bm25` 和 `store.search`；其下包含同步 Milvus 网络调用及索引构建。本地 BGE 已使用 to_thread，但这不覆盖向量存储和 BM25 路径。
- **影响与优先级理由**：慢查询或冷索引可能阻塞事件循环，影响其他请求；这是源码可见的阻塞风险，具体容量及耗时尚未测量，应在并发规模扩大前验证。
- **代码证据**：[retriever.py](../../backend/app/rag/retriever.py) 的 `hybrid_search`、`_ensure_bm25`；[store.py](../../backend/app/rag/store.py) 的 `get_client`、`all_chunks`、`search`；[local_bge.py](../../backend/app/llm/providers/local_bge.py) 的 `embed_query`、`rerank`。
- **验证方式与结论**：源码确认同步调用链；尚未执行事件循环延迟或吞吐测试，不能给出并发上限或性能提升比例。
- **建议方向（待讨论）**：隔离阻塞工作并控制排队、并发及请求预算，结合真实工作负载再确定资源配置。
- **验收条件**：模拟慢向量查询和冷索引时其他轻量请求仍可调度；超时、排队和取消可观察，真实容量以记录方法的测量结果为准。
- **2026-10-20 提案与隔离实验**：新增 ADR-0009，推荐有界工作队列、专用线程、剩余预算传播及运行调用结束后才释放容量。独立原型以合成 30 ms I/O 和真实 BM25（2001 个预分词切片）比较 W/Q=1/2、2/4、4/8，四项队列/取消/排空断言通过，初始实验配置推荐 4/8；方法、结果及环境限制见提案。未连接真实业务依赖、未改请求路径；实际多进程容量、BGE 等其他阻塞路径仍待验证，状态保持 Open。
- **2026-10-20 检索路径实施**：ADR-0009 获准后已将同步 Milvus、完整 BM25 构建/评分与融合放入有界执行器；确定性容量/取消/关闭、实际 ASGI 轻量请求、按版冷启动合并回归通过，真实 BGE/Milvus/2001 切片资源复核与双进程/浏览器验收见 [实施记录](evolution-roadmap.md#2026-10-20-首批实施记录)及 [资源数据](../adr/0009-bounded-retrieval-execution.md#2026-10-20-真实依赖资源复核)。初值 W=4/Q=8、缓存 2 版均按进程计算。BGE 自身锁/线程、其余同步路径、极端慢调用及长期连接/内存与部署吞吐/成本未完成治理，保持 In Progress。

<a id="arch-014"></a>

## ARCH-014：关键业务不变量缺少持久化回归测试

- **状态 / 优先级 / 关联 ADR**：In Progress / P1 / [ADR-0001（Accepted）](../adr/0001-managed-mysql-migrations.md)、[ADR-0002（Accepted）](../adr/0002-conversation-ticket-access.md)、[ADR-0003（Accepted）](../adr/0003-sensitive-data-boundaries.md)。
- **2026-10-20 第二批关联**：第二批验收分别见 [ADR-0004](../adr/0004-durable-write-idempotency.md)、[ADR-0005](../adr/0005-agent-outcomes-and-handoff.md)、[ADR-0006](../adr/0006-conversation-source-and-recovery.md)、[ADR-0007](../adr/0007-conversation-execution-fencing.md)，均已获用户接受并授权实施；本批实际验证见后续记录。
- **基准现象（2026-10-18）**：tests 只有模型 provider 相关的 8 项测试；评估集关注生成结果，不能替代权限、状态一致性、幂等或并发的确定性测试。此前发现的 6 种异常仅做过一次性 mock。
- **影响与优先级理由**：模型接入测试通过仍无法拦住已确认的业务问题；修复核心边界前后需要可重复的保护。
- **代码证据**：[test_llm_providers.py](../../backend/tests/test_llm_providers.py) 的 8 个 `test_*`；[run_eval.py](../../backend/eval/run_eval.py) 的 `evaluate`；[pyproject.toml](../../backend/pyproject.toml) 的 pytest 配置。
- **验证方式与结论**：源码确认测试范围；同日单元测试 8 passed，mock 结果见下表。未测量代码覆盖率，不以测试数量推导可靠性。
- **建议方向（待讨论）**：针对 ARCH-001 至 ARCH-008 等问题补充可观察业务不变量的测试；纯逻辑使用 fake，事务、隔离和提交语义使用隔离数据库验证。
- **验收条件**：权限拒绝、重复请求、建单与接管一致性、无命中、缓存故障、人工竞争和知识发布失败均有可重复验证；测试不依赖生产数据或清理业务缓存。
- **2026-10-19 覆盖增补**：随 [ARCH-008 首批修复](#arch-008) 新增初始化保护、演示装载顺序、GNU Make 入口及可选独立 MySQL 测试。当前仅有前述 11 项新测试和 3 项既有同步测试通过；GNU Make、真实 MySQL 及完整异步测试的限制见该节。权限、幂等、接管、发布失败等其他业务不变量仍待补齐，本问题保持 Open。
- **2026-10-19 第二批覆盖增补**：增加 30 项隔离验收编排与 JUnit 报告检查测试并通过；新增 Make 验收入口测试尚因缺少 GNU Make 跳过。当前本机部分运行合计 44 passed、13 skipped，独立容器实际验收仍受 Docker 访问及自动审批服务限制，详见 [ARCH-008 第二批记录](#arch-008)。本问题继续保持 Open。
- **2026-10-19 第三批覆盖增补**：ADR-0001 已获用户接受，新增 34 项离线/替身迁移测试并通过，以及 12 项待真实 MySQL 执行的迁移场景；初始化拒绝未接管库时不触碰向量服务的测试也通过。当前本机可执行部分共 79 passed、25 skipped，真实迁移及完整异步验收的缺口见 [ARCH-008 第三批记录](#arch-008)。尚未覆盖本清单其他全部业务不变量，继续保持 Open。
- **2026-10-19 验收补齐**：隔离完整套件实际取得 109 passed、零失败/错误/跳过，补齐上述 MySQL、Make 及既有异步测试的执行缺口，详见 [ARCH-008 完整隔离验收](#arch-008)。本问题继续保持 Open：权限、工具幂等、人工竞争、知识发布失败等其他业务不变量仍需随对应修复补测试，不能由当前套件数量推导已覆盖。

- **2026-10-19 试用第一批覆盖增补**：新增 20 项离线任务保护、36 项隐私边界和 6 项真实 MySQL 测试，后者在测试内部遍历角色/资源/动作矩阵。完整套件实际为 **171 passed，零失败/错误/跳过**；前端另有 7 项通信回归及构建通过，详见 [第一批验收](pilot-roadmap.md#first-batch-verification)。当前已补齐权限、敏感数据边界与评估禁用场景，幂等、恢复、人工竞争、知识发布失败等仍需后续测试，因此保持 In Progress。

- **2026-10-20 第二批覆盖**：完整隔离 **238 passed，零失败/错误/跳过**，包含图结果、幂等、真实 SQL 提交/竞争/恢复及独立 Redis 过期/投影/消费去重；前端另 15 项及构建通过。验收环境、失败修正及证据边界见 [第二批验收](pilot-roadmap.md#second-batch-verification)。ARCH-014 继续 In Progress，知识发布失败等延期能力的不变量仍待对应实施，不能由测试总数推导已经覆盖。

- **2026-10-20 第三批覆盖**：完整隔离后端 **272 passed**，前端 **26 passed**，独立真实依赖及 Chromium **15 passed**，均零失败/错误/跳过；前端类型/构建、浏览器测试类型检查通过。新增数字误判反例、脚本隔离/清理保护、知识恢复就绪边界与真实登录/断流/竞争/进程及依赖故障场景；知识维护演练恢复 66 个切片并逐字段核对，再验证引用。报告与范围见 [第三批记录](pilot-roadmap.md#third-batch-verification)。在线知识发布、完整取消等延期能力仍未完成，本问题继续 In Progress。
- **2026-10-20 下一阶段测试规划**：[ADR-0008（Proposed）](../adr/0008-versioned-knowledge-publication.md)、[ADR-0009（Proposed）](../adr/0009-bounded-retrieval-execution.md)关联的发布故障、版本重放、2001 切片完整性、有界调度、迁移和浏览器不变量已形成 [验收矩阵](evolution-roadmap.md#第一阶段验收矩阵)。本轮只有隔离设计原型实验，没有新增持久化业务回归或重跑上述历史套件，继续 In Progress。
- **2026-10-20 知识发布覆盖**：新增知识快照、并发发布与真实进程退出、版本绑定/原 UUID/延迟缓存任务、完整检索、历史引用、冷启动与取消容量、模型工件变化及隐私不变量。最终隔离后端 302、前端 26、完整真实依赖 Chromium 16 项通过并清理，证据分类见 [实施记录](evolution-roadmap.md#2026-10-20-首批实施记录)。本问题保持 In Progress，不以测试数量关闭；后续独立评估、幂等范围和生命周期治理仍需各自断言。

<a id="arch-015"></a>

## ARCH-015：人工回复、直接追加及工作台更新尚未幂等

- **状态 / 优先级**：Open / P2。
- **2026-10-20 复核证据**：[workbench.py](../../backend/app/api/workbench.py) 的 human_reply、update_ticket、takeover_conversation 与 [conversations.py](../../backend/app/api/conversations.py) 的 add_customer_message 没有客户端幂等标识。消息共享入口保证持久化和执行协调，不提供请求去重；重复请求可能重复消息或审计。接管重复请求也可能重复审计及控制版本递增。
- **本次处理边界**：确认缺口，单独追踪；本轮修正第二批已有契约的缺陷，不扩展这些公共写接口。ADR-0004 当前覆盖聊天、反馈及可信建单工具，ARCH-003 的 Resolved 仅指该范围，不能宣称“所有写请求均已幂等”。
- **下一步与验收**：启用依赖网络重试的人工工作台/直接追加流程前，明确这些操作的请求身份、冲突与查询契约，按架构规范决定扩展方案；独立 SQL 验证重复、并发重复、提交后断连仅产生一次消息/更新审计，权限与接管竞争继续成立。
- **2026-10-20 下一阶段顺序**：列入 [演进路线第三阶段](evolution-roadmap.md#阶段与依赖)，先明确新增写入口幂等/查询契约，再联合 ARCH-016 定义保留与重放边界。本轮不扩展公共写接口，保持 Open。

<a id="arch-016"></a>

## ARCH-016：持久化操作及缓存去重记录缺少保留期治理

- **状态 / 优先级**：Open / P2（规模扩大前处理，尚无容量测量）。
- **2026-10-20 复核证据**：[semantic_cache.py](../../backend/app/cache/semantic_cache.py) 的 semcache:effect 去重标记没有 TTL；操作/任务也没有自动清理。缓存答案列表每租户最多 200 条，不限制这些独立标记的总量。
- **本次判断**：不采用单独加 TTL 或成功后立即删除。缓存写入成功、任务完成提交前进程异常时会重放任务；受控人工重试也没有最大年龄，去重标记先失效会再次插入缓存。这是现有持久化补偿语义下的容量债务，不把无期限保留称为无风险。
- **下一步与验收**：容量治理须一起定义操作/任务最大重试年龄、历史结果保留期、Redis 去重生命周期及安全清理机制；在该边界确定前保留现状。验证清理边界、写缓存后故障、长期任务补偿及重放，并测量记录增长。不得仅靠删除标记宣称已解决。
- **2026-10-20 知识版本保留边界**：[ADR-0008（Proposed）](../adr/0008-versioned-knowledge-publication.md)按用户选择首批保留已发布知识快照与向量集合，不自动删除；这是待实施方案新增的容量债务，并非现有系统已有版本历史。第三阶段须联合引用寿命、回退窗口、操作/任务重放年龄设计治理；本轮不修改 TTL 或清理记录，保持 Open。
- **2026-10-20 实施后的容量债务**：ADR-0008 已 Accepted 并完成首批限定验收，现已保留全部发布快照、集合及分版缓存，失败构建也不自动清理。条目数有限的 BM25 缓存不能代表 SQL、Milvus、Redis 总容量受控；本次固定语料检索实验没有测量长期存储增长，继续 Open，联合保留与重试边界按第三阶段设计。

## 2026-10-20 第二批验收报告复核

Claude Code 报告中重复出现的范围及前端项按一个问题处理。确认并修正：超时/管线兜底不携带实体时保留 SQL 事实；聊天、反馈及失败恢复中的 SensitiveDataError 显式上抛；最终提交出口检查生成文本、卡片、引用中的动作话术，以服务端模板表达真实提交结果；人工确认的 ticket_status 与 ticket_id 一致；已关闭/解决的接管单不再复用。新建单及指针更新仍在原事务中完成，不改变 Accepted ADR。

前端对内容冲突保留原操作供查询，移除本次被拒绝的输入和占位，增加需用户明确确认的“开启新对话”。放弃等待只清除本地关联，不取消服务端执行。自动换 UUID 重发旧请求不在本次修复内。标识名称按 [路线中的术语表](pilot-roadmap.md#request-identifiers) 区分。

动作话术检查采用保守规则，命中时替换生成展示内容，可能损失部分诊断文字；它不是任意自然语言的语义证明。真实模型输出、浏览器操作和取消协议仍属准入验证边界。新增持久化回归与本次完整结果见 [复核验收记录](pilot-roadmap.md#second-batch-review)。ARCH-014 继续 In Progress，新增范围和容量缺口不计为已解决。

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
