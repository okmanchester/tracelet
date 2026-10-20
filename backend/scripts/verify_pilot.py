"""独立真实依赖和 Chromium 准入验收：python -m scripts.verify_pilot。"""

import argparse
import contextlib
import importlib.metadata
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from scripts.verify_setup import validate_report

ROOT = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def clean_compose_env(source):
    env = source.copy()
    for name in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES"):
        env.pop(name, None)
    env["COMPOSE_DISABLE_ENV_FILE"] = "1"
    return env


class Pilot:
    def __init__(self):
        from app.config import Settings

        self.settings = Settings()
        self.project = "tracelet-pilot-" + uuid4().hex
        self.report = ROOT / ".pytest_cache" / "pilot" / self.project
        self.env = clean_compose_env(os.environ)
        self.env.update({
            "TRACELET_PILOT_PROJECT": self.project,
            "TRACELET_PILOT_DB": self.project.replace("-", "_"),
            "TRACELET_PILOT_DB_PASSWORD": secrets.token_hex(20),
            "TRACELET_PILOT_LOGIN_PASSWORD": secrets.token_urlsafe(20),
            "TRACELET_PILOT_CONTROL_SECRET": secrets.token_hex(24),
            "TRACELET_PILOT_REPORT_DIR": str(self.report),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
        })
        self.compose = ["docker", "compose", "--file", str(ROOT / "compose.pilot.yml"),
                        "--project-name", self.project, "--project-directory", str(ROOT)]
        self.backend_port, self.front_port = free_port(), free_port()
        ports = {self.backend_port, self.front_port}
        for service in ("MYSQL", "REDIS", "MILVUS"):
            port = free_port()
            while port in ports:
                port = free_port()
            ports.add(port)
            self.env[f"TRACELET_PILOT_{service}_PORT"] = str(port)
        self.backend = None
        self.front = None
        self.server = None
        self.log = None
        self.children = []
        self.child_lock = threading.Lock()
        self.cleaning = False
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.release.set()
        self.model_mode = "normal"
        self.api_mode = None
        self.model_calls = 0
        self.model_sensitive = False
        self.last_request = None
        self.model_base = {
            "deepseek": self.settings.deepseek_base_url,
            "dashscope": self.settings.dashscope_base_url,
            "openai_compat": self.settings.openai_compat_base_url,
        }.get(self.settings.llm_chat_provider)

    def announce(self, message):
        print(f"[verify-pilot] {message}", flush=True)

    def command(self, args, cwd=ROOT, timeout=600, cleanup=False):
        process = self.child(args, cwd, cleanup=cleanup)
        try:
            code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            self.stop(process)
            raise
        if code:
            raise RuntimeError(f"步骤失败：{Path(str(args[0])).name}，退出码 {code}；详见本次日志")

    def compose_run(self, *args, timeout=600):
        self.command([*self.compose, *args], timeout=timeout, cleanup=bool(args and args[0] == "down"))

    def port(self, service, port):
        result = subprocess.run([*self.compose, "port", service, str(port)], env=self.env,
                                capture_output=True, text=True, check=True, timeout=30)
        value = result.stdout.strip()
        if not value.startswith("127.0.0.1:"):
            raise RuntimeError("依赖未绑定独立本机端口")
        return value.rsplit(":", 1)[1]

    def child(self, args, cwd, cleanup=False):
        with self.child_lock:
            if self.cleaning and not cleanup:
                raise RuntimeError("验收正在清理，拒绝启动新进程")
            process = subprocess.Popen(args, cwd=cwd, env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
            self.children.append(process)
            return process

    def stop(self, proc):
        if proc is not None and proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdout=self.log, stderr=subprocess.STDOUT, timeout=30, check=False)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)

    def ready(self, url, process, timeout=90):
        import httpx

        end = time.monotonic() + timeout
        with httpx.Client(trust_env=False, timeout=2) as client:
            while time.monotonic() < end:
                if process.poll() is not None:
                    raise RuntimeError("服务提前退出；详见本次日志")
                try:
                    if client.get(url).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
        raise RuntimeError("服务就绪超时")

    def start_backend(self):
        self.backend = self.child([sys.executable, "-B", "-m", "uvicorn", "app.main:app",
                                   "--host", "127.0.0.1", "--port", str(self.backend_port),
                                   "--no-access-log"], ROOT / "backend")
        self.ready(f"http://127.0.0.1:{self.backend_port}/health", self.backend)

    def inspect(self, request_id=None):
        import pymysql

        with pymysql.connect(host="127.0.0.1", port=int(self.env["MYSQL_PORT"]), user="root",
                             password=self.env["MYSQL_PASSWORD"], database=self.env["MYSQL_DB"],
                             cursorclass=pymysql.cursors.DictCursor) as db, db.cursor() as cursor:
            cursor.execute("SELECT request_id,status,conversation_id,user_id FROM operation "
                           "WHERE request_id=%s ORDER BY created_at", (request_id,))
            ops = cursor.fetchall()
            if not ops:
                return {"operations": []}
            conv_id = ops[0]["conversation_id"]
            cursor.execute("SELECT role,content,meta FROM message WHERE conversation_id=%s ORDER BY sequence", (conv_id,))
            messages = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) AS n FROM ticket WHERE conversation_id=%s", (conv_id,))
            tickets = cursor.fetchone()["n"]
            cursor.execute("SELECT transferred_to_human,collected_entities FROM conversation WHERE id=%s", (conv_id,))
            conv = cursor.fetchone()
            return {"operations": ops, "messages": messages, "tickets": tickets, "conversation": conv}

    def control(self, body):
        action = body.get("action")
        if action == "state":
            return {"model_calls": self.model_calls, "model_sensitive": self.model_sensitive,
                    "last_request": self.last_request}
        if action == "model":
            mode = body["mode"]
            if mode not in ("normal", "hold", "error"):
                raise ValueError("unknown mode")
            self.model_mode = mode
            if mode == "hold":
                self.release.clear()
            else:
                self.release.set()
            return {"ok": True}
        if action == "api":
            with self.lock:
                self.api_mode = {"path": body.get("path", "/api/chat"), "mode": body["mode"],
                                 "status": body.get("status", 500)}
            return {"ok": True}
        if action == "inspect":
            return self.inspect(body["request_id"])
        if action == "audit":
            self.command([sys.executable, "-B", "-m", "scripts.pilot_fixture", "audit"], ROOT / "backend")
            return json.loads((self.report / "privacy.json").read_text(encoding="utf-8"))
        if action == "expired_token":
            from jose import jwt

            return {"token": jwt.encode({"sub": "u_a1", "exp": 1}, self.env["JWT_SECRET"], algorithm="HS256")}
        if action == "backend_stop":
            self.stop(self.backend)
        elif action == "backend_start":
            self.start_backend()
        elif action in ("redis_stop", "redis_start", "milvus_stop", "milvus_start"):
            service, verb = action.split("_")
            self.compose_run("stop" if verb == "stop" else "start", service)
            if verb == "start":
                self.compose_run("up", "-d", "--wait", "--wait-timeout", "180", service)
        elif action == "knowledge_recovery":
            self.stop(self.backend)
            try:
                self.command([sys.executable, "-B", "-m", "scripts.pilot_fixture", "knowledge-recovery"], ROOT / "backend")
            finally:
                self.start_backend()
            return json.loads((self.report / "knowledge-recovery.json").read_text(encoding="utf-8"))
        else:
            raise ValueError("unknown action")
        return {"ok": True}

    def proxy(self):
        pilot = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # 不记录请求头、模型载荷或认证信息。

            def reply(self, code, data, content_type="application/json"):
                if not isinstance(data, bytes):
                    data = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    self.wfile.write(data)

            def do_GET(self):
                self.forward()

            def do_POST(self):
                self.forward()

            def forward(self):
                import httpx

                payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.path == "/__pilot/control":
                    if self.headers.get("X-Pilot-Control") != pilot.env["TRACELET_PILOT_CONTROL_SECRET"]:
                        self.reply(403, {"detail": "forbidden"})
                        return
                    try:
                        self.reply(200, pilot.control(json.loads(payload)))
                    except Exception as exc:  # noqa: BLE001 -- 测试控制边界，固定响应避免输出载荷。
                        pilot.announce("控制步骤失败：" + type(exc).__name__)
                        self.reply(500, {"detail": "验收控制步骤失败，详见本次日志"})
                    return
                model = self.path.startswith("/__model/")
                if not model and not self.path.startswith("/api/"):
                    self.reply(404, {})
                    return
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() not in ("host", "connection", "content-length", "accept-encoding")}
                if model:
                    with pilot.lock:
                        pilot.model_calls += 1
                        pilot.model_sensitive |= b"sk-pilot-secret-1234567890abcdefghijk" in payload
                    if not pilot.release.wait(320):
                        self.reply(504, {"error": {"message": "controlled timeout"}})
                        return
                    if pilot.model_mode == "error":
                        self.reply(503, {"error": {"message": "controlled model failure"}})
                        return
                    target = pilot.model_base.rstrip("/") + self.path[len("/__model"):]
                else:
                    target = f"http://127.0.0.1:{pilot.backend_port}" + self.path
                    if self.path == "/api/chat":
                        try:
                            pilot.last_request = json.loads(payload)["request_id"]
                        except (ValueError, KeyError):
                            pass
                mode = None
                if not model:
                    with pilot.lock:
                        if pilot.api_mode and self.path.startswith(pilot.api_mode["path"]):
                            mode, pilot.api_mode = pilot.api_mode, None
                    if mode and mode["mode"] == "status":
                        self.reply(mode["status"], {"detail": "controlled HTTP failure"})
                        return
                try:
                    with httpx.Client(trust_env=model, timeout=330) as client:
                        response = client.request(self.command, target, headers=headers, content=payload)
                    content = response.content
                    if mode and mode["mode"] == "truncate":
                        # HTTP 真服务已提交，仅截去向浏览器展示的完成事件。
                        content = content.split(b"event: done", 1)[0]
                    self.reply(response.status_code, content, response.headers.get("content-type", "application/json"))
                except (httpx.HTTPError, OSError):
                    self.reply(502, {"detail": "controlled upstream interruption"})

        class Server(ThreadingHTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def run(self):
        provider = self.settings.llm_chat_provider
        key = {"deepseek": self.settings.deepseek_api_key, "dashscope": self.settings.dashscope_api_key,
               "openai_compat": self.settings.openai_compat_api_key}.get(provider)
        self.report.mkdir(parents=True, exist_ok=False)
        self.log = (self.report / "runtime.log").open("wb")
        result = 1
        summary = {"project": self.project, "passed": False, "cleanup": False,
                   "evidence": "real JWT/SQL/Redis/Milvus/BGE/model plus separately labelled fault injection"}
        try:
            if not self.model_base or not key:
                raise RuntimeError("当前模型厂商未完整配置，准入未通过")
            if self.settings.embedding_provider != "local" or self.settings.rerank_provider != "local":
                raise RuntimeError("本准入配置要求现有 local embedding/rerank，不自动切换厂商")
            self.announce("启动独立依赖；报告：" + str(self.report))
            self.compose_run("up", "-d", "--wait", "--wait-timeout", "300")
            proxy = self.proxy()
            self.env.update({
                "MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": self.port("mysql", 3306),
                "MYSQL_USER": "root", "MYSQL_PASSWORD": self.env["TRACELET_PILOT_DB_PASSWORD"],
                "MYSQL_DB": self.env["TRACELET_PILOT_DB"],
                "REDIS_URL": f"redis://127.0.0.1:{self.port('redis', 6379)}/0",
                "MILVUS_URI": f"http://127.0.0.1:{self.port('milvus', 19530)}",
                "MILVUS_COLLECTION": "pilot_" + self.project[-32:],
                "JWT_SECRET": secrets.token_hex(32), "APP_ENV": "pilot", "EFFECT_WORKER_ENABLED": "true",
                "TRACELET_PILOT_FRONT_URL": f"http://127.0.0.1:{self.front_port}",
                "TRACELET_PILOT_CONTROL_URL": proxy + "/__pilot/control",
                "TRACELET_API_PROXY_TARGET": proxy,
                provider.upper() + "_BASE_URL": proxy + "/__model",
            })
            self.announce("迁移全新库并装载合成身份、诊断数据和固定知识")
            for module, args in (("scripts.migrate_db", ["upgrade"]), ("scripts.migrate_db", ["check"]),
                                 ("scripts.pilot_fixture", ["seed"]), ("scripts.pilot_fixture", ["ingest"])):
                self.command([sys.executable, "-B", "-m", module, *args], ROOT / "backend", timeout=900)
            self.start_backend()
            node = shutil.which("node")
            if not node:
                raise RuntimeError("缺少 Node.js")
            self.front = self.child([node, "node_modules/vite/bin/vite.js", "--host", "127.0.0.1",
                                     "--port", str(self.front_port), "--strictPort"], ROOT / "frontend")
            self.ready(self.env["TRACELET_PILOT_FRONT_URL"], self.front)
            self.announce("运行 Chromium 真实链路及独立故障场景（不自动重试测试）")
            self.command([node, "node_modules/@playwright/test/cli.js", "test", "--config", "playwright.config.ts"],
                         ROOT / "frontend", timeout=2400)
            summary["browser_tests"] = validate_report(self.report / "browser.xml")
            summary["model_calls_including_injected_attempts"] = self.model_calls
            summary["model_sensitive_marker_detected"] = self.model_sensitive
            if self.model_sensitive:
                raise RuntimeError("模型输入敏感标记检查失败")
            summary["versions"] = {name: importlib.metadata.version(name)
                                   for name in ("fastapi", "pymilvus", "openai", "sentence-transformers", "sqlalchemy")}
            summary["python"] = sys.version.split()[0]
            summary["provider"] = provider
            summary["models"] = {"small": getattr(self.settings, provider + "_model_small"),
                                 "large": getattr(self.settings, provider + "_model_large")}
            result = 0
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 -- 所有验收失败均须生成报告并执行清理。
            summary["failure"] = "验收中断" if isinstance(exc, KeyboardInterrupt) else str(exc)
            self.announce(summary["failure"])
        finally:
            self.release.set()
            with self.child_lock:
                self.cleaning = True
                children = list(reversed(self.children))
            processes_clean = True
            for process in children:
                try:
                    self.stop(process)
                except (OSError, subprocess.SubprocessError):
                    result = 1
                    processes_clean = False
            if self.server:
                self.server.shutdown()
                self.server.server_close()
            try:
                self.compose_run("down", "--volumes", "--remove-orphans", timeout=180)
                summary["cleanup"] = processes_clean
            except (OSError, RuntimeError, subprocess.SubprocessError):
                result = 1
                self.announce("清理失败，仅恢复本次项目：" + subprocess.list2cmdline(
                    [*self.compose, "down", "--volumes", "--remove-orphans"]))
            summary["passed"] = result == 0
            (self.report / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log.close()
        self.announce("准入验收通过" if result == 0 else "准入验收未通过")
        return result


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    return Pilot().run()


if __name__ == "__main__":
    sys.exit(main())
