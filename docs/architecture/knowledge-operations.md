# 知识版本构建、发布与回退

本文对应已接受的 [ADR-0008](../adr/0008-versioned-knowledge-publication.md) 和 [ADR-0009](../adr/0009-bounded-retrieval-execution.md)。命令在 `conda activate tracelet` 后于 backend 目录运行；操作目标来自该进程配置。本文不代表已经向开发或试用库部署。

## 首次接入

首次接入需要维护窗口：停止写入及全部旧 worker，保留目标数据库、源文档、旧集合与模型配置的可恢复备份。先在副本执行增量升级和结构检查，再切换目标；不得编辑已应用的迁移或将未登记库直接 stamp。

```powershell
python -m scripts.migrate_db current
python -m scripts.migrate_db upgrade
python -m scripts.migrate_db check
```

新 head 为 `0003_knowledge_releases`，保留旧 KnowledgeDocument、消息、工单和操作；新增不可变文档/切片、版本、发布记录和单例指针，旧操作的 knowledge_version 保持空值。旧消息的 v1 不会被推断成新版本。

切换前盘点旧操作及 semantic_cache 附属任务。在旧版契约下完成在途工作，或逐笔明确处置并留记录；不要给旧操作/任务补上当前新版，也不要换 UUID 重放旧请求。新版对未版本化的 AI 操作重试返回 409，未版本化的答案缓存任务会明确失败，不写入新版命名空间；已提交旧结果仍可查询。

旧缓存不需要 FLUSHDB，新答案使用版本命名空间。保留旧集合；本批不自动清理失败构建或已发布历史。受控 reset 会清除 SQL 事实和旧配置集合，但不会枚举删除历史版本的独立向量集合；这类全量环境销毁须按实际隔离资源范围另行操作，不能用普通发布或 ingest 替代。

## 构建、验证与显式发布

```powershell
python -m scripts.knowledge list
python -m scripts.knowledge build
python -m scripts.knowledge validate <knowledge_version> --timeout 60
python -m scripts.knowledge publish <knowledge_version> --expected-generation <N> --operator <operator_name> --publication-id <UUID>
```

`build` 输出新版本 ID，只从已完成的脱敏源快照构建独占集合，不改变当前指针。失败保留候选版本及固定错误，不删除当前版本；修复后重新 build 获得新 ID。`validate` 比对完整清单和真实向量数据，空知识、缺行、重复切片、维度/内容不符、索引或加载未就绪均不能进入 ready。首次指针代次为 0。

发布前从 list 取得预期代次，生成一次 UUID；发布重试使用同一 ID 和原参数。同 ID 不同参数或预期代次过时会拒绝。并发发布失去竞争后先查询当前结果，不能自动改成新代次强行覆盖。

每版清单包含 embedding 配置及本地工件摘要。仅更改模型目录内容、query-prefix 或维度也可能构成不兼容；本批不支持跨向量空间透明切换或回退。进程首次记录模型文件签名后，若发现文件变化会明确失败并要求重启，避免把内存中的旧权重认证为新工件；不要在运行中修改模型目录。CLI 的 operator 是运维声明，不能替代主机和数据库权限控制。

`python -m scripts.ingest_knowledge` / `make ingest` 现在仅构建候选版本，并输出“尚未发布”。`make setup` 不构建或发布；已显式确认的 `setup-demo` 则按初始化、种子、构建、验证、带预期代次发布的顺序执行，任何失败停止，已完成步骤不自动回滚。

## 查询结果与回退

```powershell
python -m scripts.knowledge show <knowledge_version>
python -m scripts.knowledge show --publication-id <UUID>
python -m scripts.knowledge rollback <previously_published_version> --expected-generation <N> --operator <operator_name> --publication-id <UUID>
```

提交后连接异常时，先通过原 publication-id 查询。显示结果未知时保留标识并稍后查询，不能因异常断定切换未发生。回退目标必须曾发布、验证就绪且配置兼容；回退也是一次新代次的短事务。

发布和回退只影响之后接收的 AI 轮次。原轮次、相同 UUID 重试、草稿和延迟缓存任务保持原版本；人工接管与执行租期仍能撤销 AI 提交资格。所有 worker 在线读取同一 SQL 指针，无需通过重启完成知识切换。

文档 API 支持 `knowledge_version`，省略时返回当前版本。前端从列表取得实际版本后沿用该版本请求详情；聊天引用带文档/切片 ID，可打开对应历史快照。旧消息缺少证据时展示“历史未版本化”，不链接推测的历史正文。

## 检索执行与验证

默认每进程 `RETRIEVAL_WORKERS=4`、`RETRIEVAL_QUEUE_CAPACITY=8`、`RETRIEVAL_BM25_CACHE_VERSIONS=2`。队列满是技术失败，不能解释为知识无命中；取消等待不终止已运行的同步 SDK。日志使用 op_/kv_ 前缀关联操作与版本，不输出原始查询和 SDK 异常。

先运行 `python -B -m scripts.verify_setup` 和前端测试/类型/构建；再运行 `python -B -m scripts.verify_pilot`。后者在随机独立依赖中执行真实 BGE、Milvus 2001 切片完整校验与尾部关键词召回，记录 W=1/2/4 的检索资源实验，再执行真实模型及 Chromium 场景，并通过两个独立应用进程检查在线发布、原请求重放和回退。

报告位于忽略目录 `.pytest_cache/verify`、`.pytest_cache/pilot`。资源实验是固定语料、受控并发下的检索测试，不代表完整 HTTP/LLM 服务容量；部署拓扑、真实客户数据、长期增长、服务端取消及历史保留期仍按 [演进路线](evolution-roadmap.md)继续处理。

2026-10-20 首批限定验收已通过，完整结果、报告标识与剩余边界见 [验收记录](evolution-roadmap.md#真实依赖及-chromium-验收)。验收不代替目标环境首次维护窗口及备份核查。
