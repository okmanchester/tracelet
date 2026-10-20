# 下一阶段演进：先建立可安全发布的知识体系

2026-10-20 后续决定：用户明确回复“两份adr文件已被我接受，开始执行”，ADR-0008、0009 已登记 Accepted，首批正在实施及验收。以下起草基准和规划落盘记录保留为历史；当前操作步骤见 [知识操作说明](knowledge-operations.md)。

## 规划时的基准、目标与交付（历史）

- 日期：2026-10-20（Asia/Shanghai）；源码检查基准：`23db733e21d53d7167d5d11eb34a279687fc32d3`，开始检查时工作区干净。
- 用户在本聊天要求落实下一阶段规划，选择暂不部署、整体路线加首批详细方案、知识发布优先、在线切换且单轮固定版本、运维 CLI、保留已发布历史版本。
- 本轮交付为路线、两份具体 Proposed ADR 和实施/验收任务。用户要求“架构提案保持 Proposed”，因此本轮不把规划落盘视为接受 ADR 或业务实现验收。
- 历史证据为 [第三批准入记录](pilot-roadmap.md#third-batch-verification)：后端 272 passed、前端 26 passed、独立真实依赖及 Chromium 15 passed；仅限当时合成数据、本机独立环境。本轮没有重跑这些套件，没有新增真实 Milvus、模型或部署容量结论。
- 第一阶段提案：[ADR-0008：知识版本与发布](../adr/0008-versioned-knowledge-publication.md)、[ADR-0009：检索阻塞工作有界执行](../adr/0009-bounded-retrieval-execution.md)。正式状态与决定权限沿用 [ADR 规范](../adr/README.md)。

## 阶段与依赖

| 阶段 | 问题 | 交付与完成门槛 |
| --- | --- | --- |
| 1．知识在线发布与回退 | [ARCH-007](known-issues.md#arch-007)、检索相关 [ARCH-013](known-issues.md#arch-013)、持续 [ARCH-014](known-issues.md#arch-014) | 构建失败不影响生效版本；在线发布/回退；请求、检索、缓存、引用同版；大于 2000 个切片完整检索；慢调用不直接占用事件循环 |
| 2．独立评估与基线 | [ARCH-011](known-issues.md#arch-011)、持续 ARCH-014 | 依赖和合成业务数据独立，每轮独立运行 ID；固定知识版本、数据集摘要及模型配置；连续/并发运行不清业务缓存、不写业务工单，结果可追溯 |
| 3．写入与生命周期治理 | [ARCH-015](known-issues.md#arch-015)、[ARCH-016](known-issues.md#arch-016) | 人工回复、直接追加、工作台更新/接管的请求身份、内容冲突、查询和重试契约明确；联合定义操作/任务最大重试年龄、历史结果保留及缓存去重清理边界 |
| 4．执行与容量治理 | [ARCH-012](known-issues.md#arch-012)、剩余 ARCH-013 | 确定服务端取消与提交/接管竞争；测量排队、事件循环延迟、吞吐和成本，按实测选择运行配置和扩容方式 |

先完成第一阶段再固定评估基线，避免评估使用不可追溯的知识。第三阶段先定义幂等范围，再决定保留与清理规则；不能先给去重标记增加 TTL。第四阶段复用第一阶段的观测与有界执行，但不将“停止等待”解释为服务端取消。

[ARCH-010](known-issues.md#arch-010) 保持延期；任何真实金额计算、扣费或对账启用前，须另行确定精度、货币、舍入和迁移核对方案。ARCH-014 随阶段增加业务不变量，不能以测试总数决定关闭。第二至四阶段在进入实施前各自形成具体设计；本路线不预先批准其接口或架构选择。

## 第一阶段实施任务

以下保留提案获准前的任务及退出条件；实际交付与验收另按日期记录，不覆盖规划历史。

| 任务 | 依赖与改动 | 可审阅交付/退出条件 |
| --- | --- | --- |
| K0．固定提案与资源实验 | 无；完成 ADR-0008/0009、替代方案及关联维护 | 提案仍为 Proposed；隔离实验报告与限制可定位；正式实施以用户对具体提案的决定为前提 |
| K1．版本事实及增量迁移 | ADR-0008；版本、文档/切片快照、生效指针与发布记录，聊天操作保存版本 | 新 revision 升级旧结构不丢已有消息、工单和操作；不改 0001/0002；旧引用不伪造版本 |
| K2．构建、验证、CLI 发布 | K1；独立集合、完整清单校验、预期代次切换、结果查询 | `build/validate/publish/rollback/list/show` 可用；构建不发布，旧集合不删除；并发发布只有一个预期代次成功 |
| K3．检索有界执行 | ADR-0009；同步 Milvus、BM25 构建/评分统一入口，生命周期及预算 | 满载拒绝/排队可观测；运行调用被取消等待仍占用容量；每版本 BM25 冷启动合并；配置经真实依赖复核 |
| K4．请求和任务全链路固定版本 | K1、K3；操作接收、编排、RAG、语义缓存及其补偿任务 | 原 UUID 重试仍用原版；旧延迟任务写旧版缓存；缓存及检索不可静默换版；接管/租期契约仍成立 |
| K5．文档与历史引用 | K1、K4；版本化文档 API、前端文档页及引用跳转 | 引用准确打开该版本及切片；源文件修改不改变已发布内容；旧消息显示历史未版本化 |
| K6．初始化与首次接入 | K2、K4、K5；调整 ingest、演示装载、Make/README 和准入夹具 | 安全 setup 不隐式发布；演示装载显式构建/校验/发布；维护窗口处理旧在途操作/任务，保留旧集合和恢复材料 |
| K7．故障与真实依赖验收 | K2 至 K6 | 下列矩阵逐项取得证据；之后才评估问题状态，更新实际基准、报告及剩余限制 |

核心入口及改动面由当前实现定位：

- 版本管理接替 [ingest.py](../../backend/app/rag/ingest.py) 的 `ingest_all` 发布职责；[store.py](../../backend/app/rag/store.py) 不再隐式依赖全局集合执行知识查询。
- [operations.py](../../backend/app/operations.py) 的 `receive` 在 AI 轮次接收事务保存版本；[supervisor.py](../../backend/app/agents/supervisor.py) 的 `run` 及各专业 Agent 共享该版本；最终结果仍按现有提交资格检查落库。
- [semantic_cache.py](../../backend/app/cache/semantic_cache.py) 的 `key_for/get/put`、[effects.py](../../backend/app/effects.py) 的 `consume` 显式传递原版本；路由缓存不含知识答案，不因此做全库清理。
- [retriever.py](../../backend/app/rag/retriever.py) 从版本切片快照构建 BM25，取消单份全局可变索引；[docs.py](../../backend/app/api/docs.py) 从快照读取正文。

### 接口与兼容摘要

| 入口/类型 | 推荐变化 |
| --- | --- |
| 聊天操作、执行上下文、草稿、结果及消息元信息 | 服务端产生并传递 `knowledge_version`，不由客户端正文决定；相同 UUID 不随发布更换版本 |
| `GET /api/docs`、`GET /api/docs/{doc_id}` | 可选 `knowledge_version`；返回实际版本；无参数解析当前指针；历史版本不能回退成当前正文 |
| 引用 | 保留 `index/doc_title/section/version/score`，新增 `doc_id/chunk_id`；`version` 使用知识版本标识 |
| 知识管理 CLI | `build/validate/publish/rollback/list/show`；发布/回退提供 `--expected-generation` 和 `--operator`，详情见 ADR-0008 |
| 现有 ingest | 调整为仅构建，不删除生效集合、不隐式发布；旧命令行为变化须同步说明和调用方 |

本批沿用共享知识、当前角色权限、仅脱敏内容、稳定操作 UUID、最终提交和人工接管规则。未增加租户私有知识、模型迁移、管理员管理界面或服务端取消；旧客户端新增字段及历史引用展示由前后端协调发布。

## 第一阶段验收矩阵

| 类别 | 必须覆盖的场景 | 证据与通过条件 |
| --- | --- | --- |
| 构建与发布故障 | embedding/插入/校验/SQL 切换失败；发布前后进程退出；并发发布；提交后响应丢失 | 随机独立 MySQL/Milvus；当前指针和旧版内容不被未完成构建破坏，原发布请求可确认结果 |
| 版本竞争与重放 | 两个应用进程，发布前后请求、同 UUID 重试、原草稿恢复、延迟缓存任务、回退及原接管竞争 | 独立 SQL/Redis/Milvus 和可控 barrier；每次结果/引用/缓存断言版本，绝不依靠 worker 重启达成一致 |
| 完整性与不可变性 | 至少 2001 个切片，目标在第 2000 条之后；重复正文不同文档；源文件改动；空知识；不兼容 embedding 配置 | 完整清单/摘要比对，关键词和向量召回及历史引用定位；不把缺行当作空知识或静默换版 |
| 有界执行与取消 | 慢 Milvus、BM25 冷启动、同版冷启动并发、满队列、排队过期、运行中取消、关闭生命周期 | 确定性 barrier 验证容量不超界、取消不提前释放、轻量协程可调度；真实依赖另测耗时，合成实验不代替容量验收 |
| 安全与兼容 | 合成秘密跨快照/向量/模型/缓存/日志/错误路径；旧消息；版本参数非法/不存在；CLI 失败 | 沿用脱敏不变量；认证保持；错误不泄露原文；旧引用无推测版本；失败返回非零且不声称已发布 |
| 首次迁移 | 保留旧记录和集合；无生效版本；遗留在途操作/缓存任务；演示及准入入口调整 | 独立旧结构库升级与维护窗口演练；未版本化工作有明确处置记录，不自动绑定新版或重发业务 |
| 浏览器 | 新版文档浏览、旧版引用、发布期间在途回答、回退后新请求、刷新/查询恢复 | 扩展现有 Chromium 场景，仍使用合成数据和独立真实依赖 |

先通过隔离后端与前端通信、类型/构建检查，再执行真实依赖及浏览器验收。有效、非空报告须零失败/错误/跳过且本次资源清理成功；报告、日志和快照放在已忽略目录，只清理本次随机资源。不能把新增测试的计划表述为已经通过。

首次接入采用维护窗口：停止写入和旧 worker，备份并核对遗留未版本化工作；在原契约下完成或显式处置，不删除已提交结果、不用新 UUID 自动重放。通过受控入口升级和结构检查后，构建、校验、显式发布首个版本，再启动协调发布的前后端和全部 worker。失败保持维护状态，保留旧集合与原配置；DDL 部分失败沿 ADR-0001 核查并前向修复。该窗口之后才适用在线知识切换。

## 2026-10-20 规划落盘记录

- 读取当前源码、问题清单、ADR 规范及相关 Accepted ADR；核查工作区与 Git 全历史中的 ADR 编号，分配 0008、0009，未修改 Accepted 决定。
- 新增上述两份 Proposed，决定字段留空；已知问题仅补关联与提案进展，不因规划改变状态。现有试用维护窗口约定在新方案获准、实施并验收前继续有效。
- 资源实验仅覆盖合成阻塞调用与真实 BM25 库的隔离原型；配置、数据与结果见 ADR-0009。没有连接业务依赖，没有运行评估、迁移、知识重建或部署。
- 文档检查覆盖本轮六份文档的 280 个相对文件/锚点链接、两份 Proposed 的必需章节和留空决定字段；16 项问题状态及 7 份 Accepted ADR 正文与基准一致。实验材料位于现有忽略目录，无需新增 .gitignore 规则；本轮没有重跑业务套件。

## 2026-10-20 首批实施记录

用户接受两份具体 ADR 后，已在 `23db733` 之上的工作区实现 K1 至 K6，未向业务环境部署或迁移。K0 的决定依据见两份 ADR 元信息；K7 按下述证据分别核对。

| 交付 | 实现定位 |
| --- | --- |
| 版本事实、全量脱敏快照与增量迁移 | [knowledge.py](../../backend/app/knowledge.py) 的 `build/_snapshot/validate/publish`；[0003 revision](../../backend/migrations/versions/0003_knowledge_releases.py)，保留 0001/0002 和旧事实；文档正文使用 LONGTEXT 支持超过 64 KiB 的中文快照 |
| 独占集合与完整读取 | [store.py](../../backend/app/rag/store.py) 的 `snapshot`，Strong 迭代、物理主键游标、索引/维度/数量/内容及向量摘要校验，迭代器可靠关闭 |
| 有界同步执行与不可变 BM25 | [execution.py](../../backend/app/rag/execution.py) 的 `RetrievalPool`；[retriever.py](../../backend/app/rag/retriever.py) 的 `get_index/hybrid_search`，按版完整 SQL 语料、冷启动合并和有限 LRU |
| 操作、草稿、缓存、延迟任务固定版本 | [operations.py](../../backend/app/operations.py) 的 `receive/view`；[supervisor.py](../../backend/app/agents/supervisor.py) 的 `run`；[turns.py](../../backend/app/turns.py)、[effects.py](../../backend/app/effects.py)、[semantic_cache.py](../../backend/app/cache/semantic_cache.py) |
| 历史文档与引用 | [docs.py](../../backend/app/api/docs.py)；[Docs.tsx](../../frontend/src/pages/Docs.tsx)、[Chat.tsx](../../frontend/src/pages/Chat.tsx)；服务端可信 ID 标记避免版本/切片摘要被签名脱敏规则误伤，不豁免普通模型输出 |
| 运维及初始化 | [knowledge CLI](../../backend/scripts/knowledge.py)、[演示知识装载](../../backend/scripts/setup_demo_knowledge.py)；ingest 只构建，显式发布、预期代次、发布 UUID 及查询/回退见 [操作说明](knowledge-operations.md) |

### 后端与前端检查

最终隔离后端执行 `python -B -m scripts.verify_setup`，项目 `tracelet-verify-7bbf4b788bbf46d79e6fa84b0535480f`，取得 **302 passed、零失败/错误/跳过**，本次专用容器、卷和网络已清理。JUnit 位于忽略目录 `.pytest_cache/verify/<项目>/junit.xml`。前端 `npm test -- --run` 为 **26 passed、零失败/跳过**，`npm run build`、`npm run typecheck:e2e` 通过。

| 不变量 | 证据范围及定位 |
| --- | --- |
| 构建/发布失败与可查询结果 | [test_knowledge_mysql.py](../../backend/tests/test_knowledge_mysql.py)：真实随机 MySQL；替身向量与 embedding；并发 CAS、SQL 提交失败/提交响应丢失、回退、重复 UUID 和内容冲突 |
| 真实进程退出边界 | 同文件 `test_process_exit_preserves_queryable_publication_boundary`：独立子进程以 `os._exit` 在构建、提交前、提交后退出，父进程查询指针/发布记录并重放原 ID；该测试绕过外部向量验证，证明 SQL 与进程边界，不冒充真实 Milvus 故障 |
| 请求与任务跨版 | [test_second_batch_mysql.py](../../backend/tests/test_second_batch_mysql.py)：真实 SQL/Redis，新请求绑定、原 UUID/恢复、无版本不新增消息、迟到任务只写原版缓存；沿用人工接管及租期竞争断言 |
| 排队、取消与轻量请求 | [test_retrieval_execution.py](../../backend/tests/test_retrieval_execution.py)：可控同步 barrier 下容量不提前释放、排队取消跳过、满载拒绝、过期/关闭、单版冷启动合并和 LRU；阻塞期间实际 ASGI `/health` 可返回，非真实网络负载实验 |
| 快照、身份、兼容与脱敏 | [test_knowledge_mysql.py](../../backend/tests/test_knowledge_mysql.py)、[工件检测](../../backend/tests/test_knowledge_artifacts.py)、[SDK 边界](../../backend/tests/test_knowledge_store.py)：2001 切片、重复正文来源、源文件不可变、Unicode 大正文、文档/文件名脱敏、模型文件变更拒绝、NumPy 数值转换及完整读取拒绝缺行 |
| 故障演练自身的就绪边界 | [test_verify_pilot.py](../../backend/tests/test_verify_pilot.py)：当前版本先按清单完整验证，空基线不能进入破坏性故障步骤；Milvus 健康恢复不能代替切片可见性 |

测试数量只用于报告完整性；各业务结论以上述断言与真实依赖证据为准。第二至四阶段、服务端取消和生命周期清理仍按原路线推进。

### 真实依赖及 Chromium 验收

最终完整执行 `python -B -m scripts.verify_pilot`，项目 `tracelet-pilot-28bc36d99cce4944a048e11d6bec408e`，Chromium **16 passed、零失败/错误/跳过**，`summary.json` 为 `passed=true / cleanup=true`。本次专用依赖、卷、网络及应用/浏览器进程已清理；业务环境未部署或迁移。

- 真实 MySQL、Redis、Milvus 2.4.13 / PyMilvus 2.4.15、本地 BGE、DeepSeek 配置模型与真实 JWT。客户端版本见 summary；正常模型链路和传输/依赖故障分别记录，不把代理故障注入解释为真实公网分区。
- 完整迭代验证 2001 切片，尾部独有内容可召回；BM25 完整语料与替身向量不含目标时仍能通过关键词召回的断言由后端回归补充。W=1/2/4 的真实检索实验及限制见 [ADR-0009 资源复核](../adr/0009-bounded-retrieval-execution.md#2026-10-20-真实依赖资源复核)。
- 对候选集合真实写入后注入错误维度，生效版本的 66 切片及向量/元信息保持相同。两应用进程均不重启，验证在途轮次使用原版、原 UUID 在另一进程重放相同结果、新请求和引用使用新版；回退后两进程新请求及引用恢复原版，历史新版仍可读取。
- Chromium 在回退后显式打开历史新版的文档/切片，版本标签与定位卡片正确；另验证默认文档列表到详情沿用实际版本。
- 脱敏扫描覆盖全部 SQL 模型、16 个 Redis key、4 个物理向量集合共 2140 切片（包含失败构建的部分数据）；合成秘密命中均为 0，模型输入检查也为 0。快照、正文标题/文件名与错误边界另有后端回归。

本机报告保存在已忽略的 `.pytest_cache/pilot/<项目>/`：`browser.xml`、`summary.json`、`knowledge-scale.json`、`knowledge-recovery.json`、`privacy.json` 和 runtime 日志；只登记结果与可复查入口，不纳入版本库。此前完整运行 `tracelet-pilot-b90616c29eca43b8a3fc9fb5ae8f2f38` 为 15 passed / 1 failed，清理成功，保留为失败证据。其空基线来自前一场景重启 Milvus 后数据尚未可见，现先完整 validate 并拒绝空基线，再开始故障演练；另已修复 SDK float32 快照序列化，并增加对应回归。独立定向场景 `tracelet-pilot-ee662a9991fc4e61889634c28fb6a4ad` 的 1 passed 只作诊断补充，不替代最终 16 项完整验收。

K0 至 K7 已完成本轮限定交付，ARCH-007 登记 Resolved。ARCH-013 仅完成检索路径治理，BGE 等其余路径和部署容量仍待后续，保持 In Progress；ARCH-014 持续按业务不变量补充，保持 In Progress；ARCH-016 包含本次保留的全部知识版本和缓存命名空间，仍为 Open。ADR-0008/0009 保持 Accepted，前三批历史不变。真实客户工作负载、长期内存/连接与存储增长、生产吞吐/成本、服务端取消、模型迁移和自动清理均未由本次验收证明。
