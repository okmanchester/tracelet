# Tracelet

面向 API 开放平台的多 Agent 智能客服系统。

服务对象是对外提供 API 的平台（数据查询、资质核验、风控评分等），它们的客户是企业开发者。开发者在接入和调用 API 时会持续遇到鉴权失败、签名错误、限流、回调失败、账单异常、数据质量等问题。DevSupport AI 通过多 Agent 协同，把 API 文档、调用日志、错误码、套餐账单、工单系统连接起来：**能理解开发者的技术问题、能查文档、能查日志、能解释账单、能安全脱敏，搞不定时带着完整诊断证据自动建单转人工。**

底层用 LangGraph 编排出一条 `意图路由 → 专业 Agent 并行 → 工单 → 汇总 → 安全审查` 的管线；专业 Agent（文档问答 / API 诊断 / 账单）并行执行，安全审查固定串在最后保证脱敏不被绕过。

## 这个系统能做什么

登录后在「智能助手」对话页可以直接试以下四个核心场景：

| 场景 | 示例提问 | 系统表现 |
| --- | --- | --- |
| 鉴权 401 诊断 | `我调用实名认证接口一直返回 401` | 追问 request_id → 查日志 + Key 状态 → 检索 401 文档 → 给出结论/证据/修复步骤，密钥自动脱敏 |
| 429 限流（复合问题） | `今天下午很多 429，是不是你们服务挂了？` | 诊断 / 账单 / 文档三个 Agent 并行 → 判断超 QPS → 给限速、退避、升级建议 |
| 文档问答（纯 RAG） | `签名算法怎么生成？` | 混合检索 + Rerank → 返回步骤与要点 + 文档引用 |
| 账单解释 + 高风险兜底 | `这个月费用为什么涨这么多？` | 查本月/上月用量与费用构成 → 解释原因；要求降套餐时转人工 |

内部侧的「工作台」可看工单、AI 诊断面板和 React Flow 链路可视化；「运营指标」页可看 AI 解决率、意图分布、按租户 Token 成本，并一键运行评估集。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11 + FastAPI + LangGraph |
| LLM | DeepSeek（OpenAI 兼容）：deepseek-flash / deepseek-v4-pro（多厂商可插拔，可切 DashScope 等） |
| Embedding / Rerank | 本地 BGE：bge-large-zh-v1.5（1024 维）、bge-reranker-base |
| 向量库 | Milvus 2.4 ｜ 关系库：MySQL 8 ｜ 缓存/会话：Redis 7 |
| 前端 | React 18 + Vite + Ant Design + React Flow + react-markdown |

## 系统架构

三层：**交互层**（FastAPI，REST + SSE 流式 + 鉴权/租户隔离）→ **Agent 编排层**（LangGraph Supervisor 编排多 Agent）→ **基础能力层**（RAG / 工具 / 记忆 / 安全脱敏与兜底 / 缓存与模型分层 / 可观测）。详见 `docs/tutorial/design/` 与 `docs/DevSupport-AI-技术方案设计.md`。

## 目录结构

```
backend/     后端服务（app/ 业务代码、scripts/ 数据脚本、eval/ 评估、benchmark/ 压测）
frontend/    前端应用（React + Vite）
docs/        需求 / 方案设计 / 教程文档
docker-compose.yml   基础设施（MySQL / Redis / Milvus）
Makefile     常用命令入口
```

## 环境要求

跑起来之前，本机需要准备：

- **Docker + Docker Compose**（需支持 `up --wait --wait-timeout`；Windows 使用 Docker Desktop 的 Linux 容器引擎）
- **Python 3.11+**
- **Node.js 18+**（前端）
- **一个 DeepSeek API Key**：到 [DeepSeek 开放平台](https://platform.deepseek.com/)创建 API Key（仅 Chat 走它；Embedding / Rerank 使用本地模型）。
- **本地 BGE 模型**：默认路径为 `D:/WorkSpace/AIModels/BAAI/bge-large-zh-v1.5` 与 `D:/WorkSpace/AIModels/BAAI/bge-reranker-base`，可在 `.env` 中修改。
- 可选：需要 GPU 加速时安装 CUDA 版 PyTorch（详见 `.env` 中的 `LOCAL_*_DEVICE`）。

## 快速开始

### 0. 装依赖 + 配 .env

```bash
# 后端：进入开发环境、装依赖、复制配置
cd backend
conda activate tracelet     # 或使用 venv：python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,local]"
cp .env.example .env        # 打开 .env，把 DEEPSEEK_API_KEY 填上

# 前端依赖
cd ../frontend && npm install
cd ..
```

> 后续的 `make setup / run / eval` 等命令都依赖后端 Python 环境，请在**已 `conda activate tracelet`（或激活 venv）的同一个终端**里运行。

### 1. 起基础设施

```bash
make infra-up                 # 启动基础设施，等待全部服务健康（最多 300 秒）
make infra-status             # 查看各服务状态
```

Windows PowerShell 无需安装 `make`，在项目根目录直接执行：

```powershell
docker compose config --quiet
docker compose up -d --wait --wait-timeout 300
docker compose ps
```

此步骤创建 MySQL、Redis、etcd、MinIO、Milvus 共 5 个容器，前后端继续在宿主机运行。Milvus 等待 etcd 和 MinIO 健康后才启动；启动命令等待全部服务健康后返回。首次拉取镜像另需时间，若启动失败或超时，先执行 `docker compose logs --tail 100` 查看原因，修复后重新执行启动命令。

基础设施端口仅绑定 `127.0.0.1`。数据保存在项目命名数据卷中，`docker compose down` 停止并移除容器但保留数据卷，再次启动会复用；`docker compose down -v` / `make clean` 会删除这些数据卷。当前 Milvus 2.4.13 配套使用 PyMilvus 2.4.15，后端依赖已固定该 SDK 版本。

原 `minio/minio:RELEASE.2023-03-20T20-16-18Z` 在本次准备环境时无法拉取，因此 MinIO 改用 [Milvus 官方 Compose](https://github.com/milvus-io/milvus/blob/master/deployments/docker/standalone/docker-compose.yml) 中的 `milvusdb/minio:RELEASE.2024-12-18T13-15-44Z`，健康检查同步使用镜像自带的 `mc ready local`。其余服务保留原镜像版本。

PyMilvus 2.4.15 仍使用 `pkg_resources`，后端运行依赖同时限制为 `setuptools>69,<82`，避免全新安装选中不兼容版本；Setuptools 82 已移除该模块，见 [官方变更记录](https://github.com/pypa/setuptools/blob/main/NEWS.rst)。该上限应在升级 PyMilvus 并验证其兼容性后再调整。

### 2. 安全初始化；按需装载演示数据

```bash
make setup                    # 与 make init-db 相同，受控升级 MySQL schema，确保集合存在
```

`setup` 通过 Alembic 的固定迁移历史初始化或升级 MySQL，不删除已有记录或重建知识集合，不自动执行 `seed`、`ingest`。**已有非空、未登记版本的库会被拒绝，必须先检查并显式接管，不能用重复建表代替迁移。**

在已激活的 `tracelet` 环境中重新安装后端依赖（`backend` 目录下执行 `python -m pip install -e ".[dev]"`），确保 Alembic 可用。

### 已有数据库接管与后续升级

先核对 `.env` 指向的库，保留可恢复备份并在副本检查；接管期间不要并行执行其他 schema 变更。在 `backend` 目录使用：

```powershell
python -m scripts.migrate_db current                  # 只读查询当前版本
python -m scripts.migrate_db check                    # 只读检查版本和结构
python -m scripts.migrate_db adopt-baseline --yes      # 仅在旧库结构完全匹配固定基线时登记版本
python -m scripts.migrate_db upgrade                  # 空库建表或沿版本链升级到 head
python -m scripts.migrate_db check                    # 确认版本和结构与当前模型一致
```

未登记库的 `check` 会校验固定基线，但仍以非零退出提示尚未接管。接管只登记 `0001_initial_schema`，不执行业务 DDL；缺表、额外表/列、字段类型或微秒精度、默认值、主键/唯一约束/索引/外键不符均拒绝。自动生成的约束和索引名称按结构语义比较。视图、触发器和存储例程不在本批自动接管范围，检测到时拒绝；须使用有完整 schema 可见性的账号，并人工核查 ORM 未描述的对象，不将自动比较视为对所有 MySQL 对象的证明。

写命令使用按数据库名区分的 MySQL 管理锁；锁被占用、未知版本或分支历史时拒绝。该锁不约束绕过入口的外部 DDL。MySQL DDL 中途失败可能已部分生效，不自动重试或声称回滚成功，应核查实际结构和版本后按备份及具体变更恢复。CLI 不提供任意 stamp 或破坏性 downgrade；应用启动时不自动迁移。

后续改表时，先修改模型，并在 `backend` 下使用 `python -m alembic -c alembic.ini revision -m "describe_change"` 新建 revision，审阅并填写显式 DDL，再通过受控 `upgrade`、`check` 和独立库测试验证。不要改写已应用的 revision 或 `migrations/baseline.py`。直接 Alembic 在线升级/自动生成不接受自行创建的连接，不能绕过受控入口；本批使用手工审阅的 revision，自动生成不作为可靠结构证明。设计依据见 [ADR-0001](docs/adr/0001-managed-mysql-migrations.md)。

### 演示装载与显式重置

首次体验演示场景时，在专用演示环境显式执行：

```bash
make setup-demo CONFIRM_RESET=1
```

此命令依次执行安全初始化、种子装载、知识入库，任一步失败即停止，已完成的步骤不会自动回滚。种子装载会清空调用日志、密钥、应用、接口、错误码、账单、用量、用户、租户和套餐表；知识准备会构建独立版本、完整验证，并以预期代次显式发布；保留历史集合。缺少 `CONFIRM_RESET=1` 会直接拒绝，且不启动任何数据准备步骤。

Windows PowerShell 可在项目根目录使用相同的脚本入口，无需安装 `make`：

```powershell
conda activate tracelet
Set-Location backend
python -m scripts.init_db             # 安全初始化
# 仅在确认覆盖演示数据和知识索引时执行：
python -m scripts.setup_demo --yes    # 内部顺序执行，失败即停
Set-Location ..
```

需要删除并重建**受管 MySQL 业务表、版本表及当前知识向量集合**时，单独使用 `make reset-db CONFIRM_RESET=1`；PowerShell 对应在 `backend` 下执行 `python -m scripts.init_db --recreate --yes`。单独传 `--recreate` 会被拒绝。重置会先拒绝未受管表/对象或未知版本，再删除受管表及版本表，通过迁移历史重建；不清理 Redis、不重新装载演示数据，也不保证跨存储原子性。

单独的 `make seed` 仍清空上述业务表，不检查 `CONFIRM_RESET`；`make ingest` 现在仅构建候选知识版本，不删除生效集合、不隐式发布。知识版本须再经 validate、publish 显式启用，命令与首次接入步骤见 [知识操作说明](docs/architecture/knowledge-operations.md)。

### 3. 启动服务

```bash
make run                      # 后端 :8000（另开一个终端起前端）
make front                    # 前端 :5173
make health                   # 健康检查，返回 {"status":"ok"} 即正常
```

浏览器打开 **http://localhost:5173** 登录即可体验。

**装载演示数据后的预置账号**（密码统一 `password123`；仅执行安全初始化不会创建这些账号）：

| 账号 | 角色 | 用途 |
| --- | --- | --- |
| `dev_acme` / `admin_acme` | 客户（开发者 / 管理员） | 客户侧：对话、查工单 |
| `support1` | 技术支持 | 内部侧：工作台、链路、接管回复 |
| `admin` | 管理员 | 运营指标页 |

## 端口一览

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:5173 |
| 后端 API | http://localhost:8000 （文档 `/docs`） |
| MySQL | localhost:3307 |
| Redis | localhost:6380 |
| Milvus | localhost:19531 |
| MinIO 控制台 | http://localhost:9003 |

> 宿主机端口已刻意错开常用端口，避免和本机其它服务冲突。

## 评估与压测

试用环境必须设置后端 `APP_ENV=pilot`。评估 HTTP、直接函数、CLI 和压测入口会在加载业务执行依赖与清缓存/写数据之前拒绝运行；运营指标仍可查询，页面隐藏评估操作。仅 `dev`、`test`、`eval` 允许离线任务，其余环境（含未知值）均拒绝。该限制不等于评估依赖已隔离：运行下面命令时仍须使用独立评估数据与依赖，不得指向试用或业务环境。

```bash
make eval     # 跑标准评估集，输出意图/引用/脱敏等指标
make bench    # 并发压测 + 阶段耗时分解 + 缓存优化前后对比
```

试用推进顺序、准入条件与延期限制见 [推进路线](docs/architecture/pilot-roadmap.md)。第一、二批已取得限定范围隔离验收；第三批增加独立真实依赖和 Chromium 准入入口，实际结果以路线中的最新验收记录为准。通过验收不自动部署或开放真实用户访问。

前端通信回归可在 `frontend` 目录运行 `npm test`；`npm run build` 检查类型并生成构建产物。测试使用现有 TypeScript 与 Node 内置测试器，不启动真实业务服务。

第三批真实链路验收在 `backend` 目录执行 `python -B -m scripts.verify_pilot`；它创建专用随机 MySQL、Redis、Milvus 环境，通过真实登录和配置的外部模型验证合成业务。首次运行先在 `frontend` 执行 `npm ci`、`npx playwright install chromium`，并准备后端 `[dev,local]` 依赖和本地 BGE 模型。完整准备、故障注入边界、报告和清理方法见 [准入验收说明](docs/architecture/pilot-acceptance.md)。

聊天、反馈和操作查询支持“停止等待”，保留原操作 UUID 供稍后查询；此操作不申请服务端取消。断流和停止后先查询结果，再按服务端返回状态重试原请求；不会自动换 UUID 重放。

## 常见问题

- **`make setup` 报 Milvus 连接失败**：先执行 `make infra-up`（或上述 PowerShell 启动命令）并确认成功；失败时查看 `docker compose ps -a` 和 `docker compose logs --tail 100`。
- **后端启动报数据库/Redis 连不上**：确认 `make infra-up` 已起、`.env` 里的端口与 `docker-compose.yml` 一致（3307 / 6380 / 19531）。
- **对话报 LLM 调用失败**：检查 `.env` 的 `DEEPSEEK_API_KEY` 是否已填、账户是否有额度；如需切回 DashScope，把 `LLM_CHAT_PROVIDER=dashscope` 并填 `DASHSCOPE_API_KEY`。
- **本地模型加载失败**：确认 `.env` 的 `LOCAL_EMBEDDING_MODEL_PATH` / `LOCAL_RERANK_MODEL_PATH` 存在，且已执行 `pip install -e ".[local]"`；首次请求会加载模型，约多 2–5 秒。
- **更换 Embedding 模型后检索异常**：本批不支持跨向量空间透明切换；需按 [知识操作说明](docs/architecture/knowledge-operations.md) 核查配置、构建并验证兼容版本。答案缓存按知识版本隔离，不使用 FLUSHDB 作为发布步骤。
- **端口被占用**：改 `docker-compose.yml` 的宿主机端口映射，并同步改 `.env`。
- **想重置业务表和知识集合**：`make reset-db CONFIRM_RESET=1`；需要演示数据时再执行 `make setup-demo CONFIRM_RESET=1`。`make clean` 会停容器并删除全部项目数据卷，范围更大。

所有命令都收在 `Makefile` 里；`make` 不带参数执行第一个目标 `infra-up`。

## 初始化安全回归验证

推荐使用隔离验收入口。在项目根目录、已激活的 `tracelet` 环境中运行：

```bash
make verify-setup
```

Windows PowerShell 无需安装 GNU Make：

```powershell
conda activate tracelet
Set-Location backend
python -m scripts.verify_setup
```

前置条件是 Docker 的 Linux 容器引擎可访问、Docker Compose v2 可用，并且构建时能够下载基础镜像、系统包和 Python 依赖。宿主机只执行标准库编排脚本；GNU Make 和完整后端测试在 Python 3.12 测试镜像中运行，使用现有 `[dev]` 依赖，不安装 `[local]` 模型依赖。

入口只使用独立的 `compose.verify.yml`。每次生成 `tracelet-verify-<随机值>` 项目名，启动临时 MySQL 8.0 和 Redis，等待健康后运行完整后端测试。MySQL 数据使用 tmpfs；不发布宿主机端口，不复用业务容器、网络或数据卷，不挂载宿主目录或 Docker socket。运行时使用内部网络；真实 MySQL 测试仍只操作它自己生成的一次性库，Redis 测试使用本次独立服务，Milvus、模型及演示子进程使用现有测试替身。真实 Milvus、BGE、模型与浏览器另由 `verify_pilot` 验证。

构建使用 `Dockerfile.verify.dockerignore` 白名单，只发送所需源码、测试、迁移配置及历史、依赖声明和 Makefile，不发送 `.env`、凭证或模型。宿主机的 Compose 文件、项目名、profiles 和 env-file 环境变量不会带入本次编排。

通过条件同时检查容器退出码和 JUnit 报告：退出码为零、报告存在且有效、测试数大于零、失败/错误/跳过均为零，并且本次资源清理成功。测试跳过、依赖启动失败、报告复制失败或统计不一致都不能算通过。

报告保存在项目根目录 `.pytest_cache/verify/<本次项目名>/junit.xml`，不进入 Git。容器完成后先复制报告，再用同一配置文件和项目名清理容器、网络和卷；构建镜像及构建缓存保留供 Docker 管理。正常失败和 Ctrl+C 也会尝试清理；清理失败会返回非零并打印仅针对本次项目的恢复命令。强制终止进程或 Docker 不可用时，可在引擎恢复后于项目根目录执行（替换成本次输出的项目名）：

```bash
docker compose --file compose.verify.yml --project-name tracelet-verify-<本次随机值> down --volumes --remove-orphans
```

镜像构建或依赖启动失败时可能没有 JUnit 报告，应以终端错误为准，修复环境后重新运行入口。[Compose up](https://github.com/docker/compose/blob/main/docs/reference/compose_up.md) 的 `--exit-code-from` 用于取得测试服务退出码，后续报告检查和清理仍由编排脚本独立完成。

也可在 `tracelet` 环境的 `backend` 目录直接运行本机测试：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -B -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin
```

数据准备和验收编排单元测试使用存储或子进程替身，不调用真实 Docker；Make 入口测试在临时目录运行真实 GNU Make，无 GNU Make 时跳过。这种本机部分运行不等于上述零跳过的隔离验收。

真实 MySQL 验证默认跳过。若有允许创建、删除测试库的专用 MySQL 管理连接，可设置 `TRACELET_TEST_MYSQL_ADMIN_URL`（`mysql+pymysql` URL），再运行 `python -B -m pytest tests/test_init_db_mysql.py tests/test_schema_migrations_mysql.py -q -p no:cacheprovider -p pytest_asyncio.plugin`。测试只操作随机生成的 `tracelet_init_test_<随机值>` 数据库，结束后删除该库；不使用应用配置库，Milvus 仍使用替身。迁移测试覆盖旧库接管、结构漂移拒绝、重复升级、管理锁、显式重置，以及临时测试 revision 的增量变更和非事务性 DDL 失败；不向生产迁移历史添加演示字段。不要将连接凭证写入版本管理。

## 深入了解

- [系统已知问题与验收条件](docs/architecture/known-issues.md)
- [下一阶段演进路线](docs/architecture/evolution-roadmap.md)
- [知识版本构建、发布与回退](docs/architecture/knowledge-operations.md)
- [ADR 规范、模板与决策索引](docs/adr/README.md)
- 完整教程（需求分析 / 方案设计 / 代码实践 / 简历面试，共 31 篇）：`docs/tutorial/`
- 技术方案设计：`docs/DevSupport-AI-技术方案设计.md`
- 产品需求：`docs/DevSupport-AI-PRD-Lite.md`
- 性能优化与验收记录：`docs/PERFORMANCE.md`、`docs/VERIFICATION.md`
