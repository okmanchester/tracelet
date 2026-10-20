# 第三批准入验收操作说明

本入口验证既有试用路线的真实链路，不部署业务环境，不自动开放真实用户访问。实现依据为 [试用路线](pilot-roadmap.md)，权限、脱敏、幂等、事实来源和接管规则继续遵循 ADR-0002 至 0007。

## 执行前准备

- 使用 `conda activate tracelet`，安装后端 `[dev,local]` 依赖及前端锁文件依赖。
- Docker Linux 引擎可访问；首次运行允许拉取 MySQL 8.0、Redis 7.2、Milvus 2.4.13 及固定版本的 etcd/MinIO 镜像。
- 在 backend 的本地配置中保留当前模型厂商凭证及本地 BGE 路径。入口要求 local embedding/rerank，不自动切换厂商；模型调用可能产生现有账户费用，只运行准入场景。
- 在 frontend 执行 `npx playwright install chromium`。浏览器依赖采用项目锁文件中的 Playwright 版本；本轮仅验证 Chromium，不代表其他浏览器已经通过。

先运行完整回归，再运行准入：

```powershell
conda activate tracelet
Set-Location backend
python -B -m scripts.verify_setup
Set-Location ../frontend
npm test
npm run typecheck:e2e
npm run build
Set-Location ../backend
python -B -m scripts.verify_pilot
```

## 数据、凭证和资源边界

`verify_pilot` 独立使用 `compose.pilot.yml`，忽略继承的 Compose 项目、配置和 env-file 选择。每次创建 `tracelet-pilot-<随机值>` 项目，全部宿主端口随机分配且仅绑定 `127.0.0.1`。MySQL、Redis 和 Milvus 不复用业务容器、卷或数据；MySQL 使用临时内存文件系统，MinIO/Milvus 的卷随本次项目清理。

全新 SQL 库经受控迁移后，装载两个客户租户、每租户两个开发者及一个客户管理员，以及内部 support/admin。业务调用和过期 Key 是合成样本，知识来自仓库固定文档。身份通过真实登录和 JWT 验证取得，不覆盖服务端身份依赖。脚本会拒绝不匹配的随机数据库和知识集合名称，不能拿来装载现有业务库。

后端、前端、测试代理在宿主机独立进程运行。数据库密码、JWT 密钥、登录密码和代理控制密钥每次随机生成；不写入配置文件。前端仅通过同源代理访问验收后端。模型凭证只用于配置的真实模型服务，测试控制接口仅存在于独立验收代理，使用随机控制密钥保护。

浏览器使用独立上下文、单 worker、零自动重试；默认关闭认证状态文件、HAR、trace、截图和录像。所有报告和日志进入 `.pytest_cache/pilot/<本次项目>/`，不进入 Git，不记录模型请求正文或认证头。故障报告仍须按本地验收材料管理，不能直接作为公开附件发布。

## 场景及证据解释

| 场景 | 核对内容 |
| --- | --- |
| 真实业务 | 登录、RAG 引用、401 诊断、反馈转人工、内部工作台回复、客户历史 |
| 权限与隐私 | 真实 JWT 下遍历角色和会话边界；检查合成标记是否进入模型请求、SQL、Redis 和 Milvus |
| 通信 | HTTP 错误、202、两种 409、正常完成、截去 done、离线、停止等待、刷新与重新登录 |
| 提交与恢复 | 同 UUID 复用结果、接管阻止旧 AI 提交、进程中断后租期恢复、真实 Redis/Milvus 停止与恢复、受控模型错误 |
| 知识版本发布 | 对候选集合在真实写入后注入错误维度，确认生效集合未变；两个独立应用进程不重启，验证在途旧版、原 UUID 跨进程重放、新请求切版与回退 |
| 语料完整性与资源 | 真实 BGE/Milvus 构建并迭代校验 2001 个切片，召回尾部独有内容；固定语料、8 请求/轮、三轮，比较 W/Q=1/2、2/4、4/8 的检索资源配置 |
| 历史文档 | 文档列表返回实际版本，引用通过 doc_id/chunk_id 打开同版快照；回退后历史版本仍可读取 |

知识校验使用 Strong 迭代读取及 SQL 不可变文档/切片清单，不受原默认 2000 条预览限制。校验向量身份、元信息、数量、维度和摘要；SDK 主键用于游标而不参与内容身份。空清单、超时、不完整或不一致均拒绝 ready。知识发布控制只存在于独立验收代理，应用未增加测试端点；正式运维入口见 [知识操作说明](knowledge-operations.md)。

停止等待只结束浏览器当前请求，不申请服务端取消；结果可能已经提交、仍在执行或需等待租期后重试。UUID 保留，先查询后按返回状态重试，不自动生成新 UUID 重放旧请求。开启新对话清除本地关联前明确提示旧请求仍可能完成。

代理截去 done 属于可控传输故障，模型错误属于故障注入，不能当成真实公网网络分区。正常场景必须经过实际 MySQL、Redis、Milvus、本地 BGE 和配置的外部模型。本版入口验证 ADR-0008 的在线知识切换；既有第三批 66 切片停服恢复记录保留为历史，不能据此倒推当时已具备在线发布。

## 判定、失败与清理

`browser.xml` 必须存在、统计一致且测试数大于零，失败、错误、跳过均为零；浏览器进程退出码也必须为零。`summary.json` 的 `passed` 仅在测试和资源清理全部成功时为 true。缺凭证、模型不可用、依赖启动失败、服务退出、报告缺失及资源清理失败均不计为通过。

完成、普通失败及 Ctrl+C 都清理本次进程和 Compose 资源，包括故障代理启动的辅助脚本；清理开始后拒绝迟到的进程启动。强制终止宿主进程或 Docker 不可用时无法保证即时清理；恢复引擎后，使用终端记录的本次项目名执行：

```powershell
docker compose --file compose.pilot.yml --project-name tracelet-pilot-<本次随机值> down --volumes --remove-orphans
```

不要运行业务 Compose 的 `down -v`，也不要按名称前缀批量删除其他项目。准入测试失败时先检查本次报告和运行日志；修正后重新运行，不合并不同失败运行的局部结果来宣称整体验收通过。

ARCH-012 的完整服务端取消继续待办，ARCH-014 继续覆盖后续能力。ARCH-007、010、011、013、015、016 沿用既有条件：固定知识、账单只读、禁用评估/压测、不宣称容量、不对工作台写请求增加自动重试、不提前清理幂等记录。

浏览器测试的隔离与离线接口依据 [Playwright 浏览器上下文文档](https://github.com/microsoft/playwright/blob/main/docs/src/browser-contexts.md)及[官方离线测试](https://github.com/microsoft/playwright/blob/main/tests/library/browsercontext-basic.spec.ts)，2026-10-20 经 Context7 核查。
