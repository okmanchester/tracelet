.PHONY: infra-up infra-down infra-status init-db reset-db seed ingest setup setup-demo verify-setup run front health clean

# ---------- 基础设施（MySQL / Redis / Milvus）----------
infra-up:        ## 启动基础设施并等待健康检查通过
	docker compose up -d --wait --wait-timeout 300

infra-down:      ## 停止基础设施
	docker compose down

infra-status:    ## 查看基础设施状态
	docker compose ps

# ---------- 数据准备（在 backend 下执行）----------
init-db:         ## 受控升级 MySQL schema，确保 Milvus collection 存在（旧库须先接管）
	cd backend && python -m scripts.init_db

reset-db:        ## 删除并重建受管 MySQL 表、版本表和 Milvus collection（需 CONFIRM_RESET=1）
ifneq ($(CONFIRM_RESET),1)
	$(error reset-db 会删除数据；确认后使用 make reset-db CONFIRM_RESET=1)
endif
	cd backend && python -m scripts.init_db --recreate --yes

seed:            ## 清空种子涉及的业务表，再写入演示数据
	cd backend && python -m scripts.seed_data

ingest:          ## 删除并重建知识向量集合，再写入知识库切片
	cd backend && python -m scripts.ingest_knowledge

setup: init-db   ## 安全初始化/升级；不装载种子、不重建知识库

setup-demo:      ## 覆盖演示业务数据和知识索引（需 CONFIRM_RESET=1）
ifneq ($(CONFIRM_RESET),1)
	$(error setup-demo 会覆盖数据；确认后使用 make setup-demo CONFIRM_RESET=1)
endif
	cd backend && python -m scripts.setup_demo --yes

verify-setup:    ## 在独立容器和临时 MySQL 中验收初始化安全，结束后清理本次资源
	cd backend && python -m scripts.verify_setup

# ---------- 运行 ----------
run:             ## 启动后端 :8000
	cd backend && uvicorn app.main:app --reload

front:           ## 启动前端 :5173
	cd frontend && npm run dev

health:          ## 健康检查
	curl -s http://localhost:8000/api/health

# ---------- 评估 / 压测 ----------
eval:            ## 跑标准评估集（意图/引用/脱敏等指标）
	cd backend && python -m eval.run_eval

bench:           ## 并发压测 + 阶段耗时分解 + 缓存优化对比
	cd backend && python -m benchmark.loadtest

# ---------- 清理 ----------
clean:           ## 停止并清理容器与数据卷
	docker compose down -v
