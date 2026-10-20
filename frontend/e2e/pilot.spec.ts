import { test, expect, Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

const secret = "sk-pilot-secret-1234567890abcdefghijk";
const ragQuestion = "Webhook 回调收不到怎么排查？请按文档说明。";

async function control(action: string, args: Record<string, unknown> = {}) {
  const response = await fetch(process.env.TRACELET_PILOT_CONTROL_URL!, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Pilot-Control": process.env.TRACELET_PILOT_CONTROL_SECRET! },
    body: JSON.stringify({ action, ...args }), signal: AbortSignal.timeout(180_000),
  });
  expect(response.status, `control ${action}`).toBe(200);
  return response.json();
}

async function login(page: Page, name = "a1") {
  await page.goto("/login");
  await page.getByLabel("用户名").fill("pilot_" + name);
  await page.getByLabel("密码", { exact: true }).fill(process.env.TRACELET_PILOT_LOGIN_PASSWORD!);
  await page.getByRole("button", { name: /^登\s*录$/ }).click();
  await expect(page.getByRole("menu")).toBeVisible();
}

async function api(page: Page, path: string, body?: unknown) {
  return page.evaluate(async ({ path, body }) => {
    const response = await fetch("/api" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { Authorization: `Bearer ${localStorage.getItem("token")}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    let data: any;
    if (response.headers.get("content-type")?.includes("text/event-stream")) {
      const match = text.match(/event: done\r?\ndata: (.+)/);
      data = match ? JSON.parse(match[1]) : null;
    } else { try { data = JSON.parse(text); } catch { data = null; } }
    return { status: response.status, data };
  }, { path, body });
}

async function send(page: Page, text: string) {
  await page.getByPlaceholder("描述你的问题…").fill(text);
  await page.getByRole("button", { name: /^发\s*送$/ }).click();
}

async function accepted(page: Page) {
  const id = await page.evaluate(() => JSON.parse(sessionStorage.getItem("tracelet:pending:t_a:u_a1")!).request_id);
  await expect.poll(async () => {
    return (await control("inspect", { request_id: id })).operations.length > 0;
  }, { timeout: 30_000 }).toBe(true);
  return id;
}

async function result(page: Page, id: string) {
  let response: any;
  await expect.poll(async () => {
    response = await api(page, `/operations/chat/${id}`);
    return response.data?.operation_status;
  }, { timeout: 320_000, intervals: [500, 1000, 2000] }).toBe("succeeded");
  return response.data;
}

test.afterEach(async () => {
  await control("model", { mode: "normal" });
});

test("真实链路：RAG 引用、输入脱敏、反馈转人工、工作台回复及客户历史", async ({ page, browser }) => {
  await login(page);
  await send(page, `${ragQuestion} api_key=${secret}`);
  await expect(page.getByRole("button", { name: "已解决", exact: true })).toBeVisible({ timeout: 320_000 });
  const id = (await control("state")).last_request;
  const completed = await result(page, id);
  expect(completed.intent).toBe("doc_qa");
  expect(completed.citations?.length).toBeGreaterThan(0);
  expect(completed.need_human).toBe(false);
  const docs = await api(page, "/docs");
  for (const citation of completed.citations) {
    expect(docs.data.documents.some((d: any) => d.title === citation.doc_title)).toBe(true);
  }
  const persisted = await control("inspect", { request_id: id });
  expect(JSON.stringify(persisted)).not.toContain(secret);
  expect((await control("state")).model_sensitive).toBe(false);
  await expect.poll(async () => {
    const effects = (await api(page, `/operations/chat/${id}`)).data.effects;
    return effects.done > 0 && Object.keys(effects).every((key) => key === "done");
  }, { timeout: 30_000 }).toBe(true);
  const privacy = await control("audit");
  expect(Object.values(privacy.sql_hits).every((count) => count === 0)).toBe(true);
  expect(privacy.redis_hits + privacy.milvus_hits).toBe(0);
  await page.getByRole("button", { name: "需要人工", exact: true }).click();
  await expect(page.getByText("已转人工", { exact: true })).toBeVisible();
  const history = await api(page, `/conversations/${completed.conversation_id}`);
  expect(history.data.conversation.transferred_to_human).toBe(true);
  const tickets = await api(page, "/tickets");
  const ticket = tickets.data.tickets.find((t: any) => t.conversation_id === completed.conversation_id);
  // 工单列表未提供 conversation_id 时，通过刚完成操作的 SQL 计数确认归属。
  expect((await control("inspect", { request_id: id })).tickets).toBe(1);
  const context = await browser.newContext();
  try {
    const support = await context.newPage();
    await login(support, "support");
    await support.goto("/workbench");
    const internalTickets = await api(support, "/workbench/tickets");
    const target = ticket || internalTickets.data.tickets[0];
    await support.getByRole("row").filter({ hasText: target.ticket_id }).click();
    await support.getByPlaceholder("编辑回复内容，发送后客户可在「我的会话」中看到").fill("准入人工回复：请按文档检查回调配置。");
    await support.getByRole("button", { name: "发送回复给客户", exact: true }).click();
    await expect(support.getByText("已回复客户", { exact: false })).toBeVisible();
  } finally { await context.close(); }
  await page.goto("/conversations");
  await page.getByText(completed.conversation_id, { exact: false }).click();
  await expect(page.getByText("准入人工回复：请按文档检查回调配置。", { exact: true })).toBeVisible();
});

test("真实诊断：401 调用证据与已过期密钥", async ({ page }) => {
  await login(page);
  const id = randomUUID();
  const response = await api(page, "/chat", { request_id: id, message: "请诊断这次接口401报错，request_id=req_pilot_a，错误码 AUTH_KEY_EXPIRED。" });
  expect(response.status).toBe(200);
  const completed = response.data;
  expect(completed.intent).toBe("api_error");
  expect(completed.answer.length).toBeGreaterThan(0);
  expect(JSON.stringify(completed)).toContain("AUTH_KEY_EXPIRED");
  expect(JSON.stringify(completed)).not.toContain("req_pilot_b");
  const sql = await control("inspect", { request_id: id });
  expect(sql.operations).toHaveLength(1);
  expect(sql.messages.filter((m: any) => m.role === "assistant")).toHaveLength(1);
});

test("真实 JWT 权限矩阵：本人、同租户他人、跨租户及内部角色", async ({ browser }) => {
  for (const role of ["a1", "a2", "am", "b1", "b2", "bm", "support", "admin"]) {
    const context = await browser.newContext();
    try {
      const page = await context.newPage();
      await login(page, role);
      const internal = ["support", "admin"].includes(role);
      for (const owner of ["a1", "a2", "am", "b1", "b2", "bm"]) {
        const readable = internal || role === owner || (role.endsWith("m") && role[0] === owner[0]);
        expect((await api(page, `/conversations/conv_${owner}`)).status, `${role} reads ${owner}`).toBe(readable ? 200 : 403);
        if (role !== owner) {
          expect((await api(page, `/conversations/conv_${owner}/messages`, { content: "禁止代发" })).status).toBe(403);
          const deniedId = randomUUID();
          expect((await api(page, "/chat", { request_id: deniedId, conversation_id: `conv_${owner}`, message: "禁止代聊" })).status).toBe(403);
          expect((await control("inspect", { request_id: deniedId })).operations).toHaveLength(0);
          expect((await api(page, "/feedback", { request_id: randomUUID(), conversation_id: `conv_${owner}`, type: "need_human" })).status).toBe(403);
        }
      }
      const list = await api(page, "/conversations");
      if (!internal && !role.endsWith("m")) {
        expect(list.data.conversations.filter((c: any) => /^conv_[ab][12m]$/.test(c.id)).map((c: any) => c.id)).toEqual([`conv_${role}`]);
      }
      if (!internal) expect((await api(page, "/workbench/tickets")).status).toBe(403);
    } finally { await context.close(); }
  }
});

test("HTTP 失败：明确拒绝结束等待；服务异常保留 UUID", async ({ page }) => {
  await login(page);
  for (const status of [401, 403, 404, 422, 500]) {
    await control("api", { mode: "status", status });
    await send(page, "HTTP 准入合成请求");
    await expect(page.getByRole("button", { name: "停止等待", exact: true })).toHaveCount(0);
    await expect(page.getByText("有待确认的请求", { exact: true })).toHaveCount(status === 500 ? 1 : 0);
  }
});

test("停止等待、刷新与原 UUID 查询恢复；旧回调不得覆盖页面", async ({ page }) => {
  await login(page);
  await control("model", { mode: "hold" });
  await send(page, "请介绍接口文档的用途，准入停止等待场景。");
  const id = await accepted(page);
  await page.getByRole("button", { name: "停止等待", exact: true }).click();
  await expect(page.getByRole("button", { name: "查询进度", exact: true })).toBeEnabled();
  const stored = await page.evaluate(() => Object.entries(sessionStorage).filter(([k]) => k.startsWith("tracelet:pending:")));
  expect(JSON.stringify(stored)).toContain(id);
  expect(JSON.stringify(stored)).not.toContain("停止等待场景");
  await page.reload();
  await control("model", { mode: "normal" });
  await result(page, id);
  await page.getByRole("button", { name: "查询进度", exact: true }).click();
  await expect(page.getByText("有待确认的请求", { exact: true })).toHaveCount(0);
  const sql = await control("inspect", { request_id: id });
  expect(sql.messages.filter((m: any) => m.role === "user")).toHaveLength(1);
  expect(sql.messages.filter((m: any) => m.role === "assistant")).toHaveLength(1);
});

test("真实响应截去 done 后查询已提交结果，并验证重复提交", async ({ page }) => {
  await login(page);
  const id = randomUUID();
  // 已处于人工模式的合成会话，不消耗外部模型，仍通过真实接收/提交事务。
  const payload = { request_id: id, conversation_id: "conv_a1", message: "准入重复与截断" };
  await control("api", { mode: "truncate" });
  const response = await api(page, "/chat", payload);
  expect(response.status).toBe(200);
  expect(response.data).toBeNull();
  const completed = await result(page, id);
  const repeated = await api(page, "/chat", payload);
  expect(repeated.data.message_id).toBe(completed.message_id);
  const sql = await control("inspect", { request_id: id });
  expect(sql.operations).toHaveLength(1);
  expect(sql.messages.filter((m: any) => m.content === payload.message)).toHaveLength(1);
  // 再通过真实页面消费一次截断流。
  await control("api", { mode: "truncate" });
  await send(page, "请转人工，准入页面截断验证。");
  await expect(page.getByRole("button", { name: "查询进度", exact: true })).toBeEnabled({ timeout: 320_000 });
  const pageId = (await control("state")).last_request;
  await result(page, pageId);
  await page.getByRole("button", { name: "查询进度", exact: true }).click();
  await expect(page.getByText("有待确认的请求", { exact: true })).toHaveCount(0);
});

test("浏览器离线、查询凭证过期及重新登录保留原操作", async ({ page, context }) => {
  await login(page);
  await context.setOffline(true);
  await send(page, "离线未发送的合成问题");
  await expect(page.getByRole("button", { name: "查询进度", exact: true })).toBeEnabled();
  const stored = await page.evaluate(() => sessionStorage.getItem("tracelet:pending:t_a:u_a1"));
  await context.setOffline(false);
  const expired = await control("expired_token");
  await page.evaluate((token) => localStorage.setItem("token", token), expired.token);
  await page.getByRole("button", { name: "查询进度", exact: true }).click();
  await expect(page.getByText("登录已失效，请重新登录后查询原请求", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("tracelet:pending:t_a:u_a1"))).toBe(stored);
  await login(page);
  await expect(page.getByText("有待确认的请求", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查询进度", exact: true }).click();
  await expect(page.getByText("尚未查到已接收请求，可重试原请求", { exact: true })).toBeVisible();
});

test("停止反馈等待后查询同一操作，不重放反馈或重复建单", async ({ page }) => {
  await login(page);
  const id = randomUUID();
  expect((await api(page, "/chat", { request_id: id, conversation_id: "conv_a1", message: "反馈等待验证" })).status).toBe(200);
  await page.evaluate((request_id) => sessionStorage.setItem("tracelet:pending:t_a:u_a1",
    JSON.stringify({ request_id, kind: "chat" })), id);
  await page.reload();
  await page.getByRole("button", { name: "查询进度", exact: true }).click();
  await expect(page.getByRole("button", { name: "需要人工", exact: true })).toBeVisible();
  let release!: () => void;
  const barrier = new Promise<void>((resolve) => { release = resolve; });
  let committed!: () => void;
  const received = new Promise<void>((resolve) => { committed = resolve; });
  await page.route("**/api/feedback", async (route) => {
    const response = await route.fetch();
    committed();
    await barrier;
    await route.fulfill({ response }).catch(() => {});
  });
  await page.getByRole("button", { name: "需要人工", exact: true }).click();
  await received;
  await page.getByRole("button", { name: "停止等待", exact: true }).click();
  const pending = await page.evaluate(() => JSON.parse(sessionStorage.getItem("tracelet:pending:t_a:u_a1")!));
  expect(pending.kind).toBe("feedback");
  release();
  await page.getByRole("button", { name: "查询进度", exact: true }).click();
  await expect(page.getByText("有待确认的请求", { exact: true })).toHaveCount(0);
  expect((await api(page, `/operations/feedback/${pending.request_id}`)).data.operation_status).toBe("succeeded");
});

test("停止后开启新对话，旧请求完成不覆盖新页面", async ({ page }) => {
  await login(page);
  await control("model", { mode: "hold" });
  await send(page, "请转人工，准入旧响应隔离。");
  const id = await accepted(page);
  await page.getByRole("button", { name: "停止等待", exact: true }).click();
  await page.getByRole("button", { name: "开启新对话", exact: true }).click();
  await page.getByRole("button", { name: /^确\s*定$/ }).click();
  await control("model", { mode: "normal" });
  await result(page, id);
  await expect(page.getByText("描述接口报错、接入或账单问题，AI 将基于可用证据协助处理。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "已解决", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem("tracelet:pending:t_a:u_a1"))).toBeNull();
});

test("处理中 202、两类 409 与人工接管阻止旧 AI 提交", async ({ page, browser }) => {
  await login(page);
  await control("model", { mode: "hold" });
  const id = randomUUID();
  const payload = { request_id: id, message: "请说明 API 接入文档，准入接管竞争。" };
  const running = api(page, "/chat", payload);
  let sql: any;
  await expect.poll(async () => {
    sql = await control("inspect", { request_id: id });
    return sql.operations.length;
  }).toBe(1);
  const conv = sql.operations[0].conversation_id;
  expect((await api(page, "/chat", payload)).status).toBe(202);
  const conflict = await api(page, "/chat", { ...payload, message: "不同内容" });
  expect(conflict.status).toBe(409);
  expect(conflict.data.detail.code).toBe("request_conflict");
  const busy = await api(page, "/chat", { request_id: randomUUID(), conversation_id: conv, message: "另一轮" });
  expect(busy.status).toBe(409);
  expect(busy.data.detail.code).toBe("conversation_busy");
  const supportContext = await browser.newContext();
  try {
    const support = await supportContext.newPage();
    await login(support, "support");
    expect((await api(support, `/workbench/conversations/${conv}/takeover`, {})).status).toBe(200);
  } finally { await supportContext.close(); }
  await control("model", { mode: "normal" });
  await running;
  const after = await control("inspect", { request_id: id });
  expect(after.messages.filter((m: any) => m.role === "assistant")).toHaveLength(0);
  expect(after.tickets).toBe(0);
  expect(after.conversation.transferred_to_human).toBe(1);
});

test("后端进程中断、租期到期后原 UUID 重试不重复消息", async ({ page }) => {
  await login(page);
  await control("model", { mode: "hold" });
  const id = randomUUID();
  const payload = { request_id: id, message: "请转人工，准入进程恢复。" };
  const running = api(page, "/chat", payload).catch(() => null);
  await expect.poll(async () => (await control("inspect", { request_id: id })).operations.length).toBe(1);
  await control("backend_stop");
  await control("model", { mode: "normal" });
  await running;
  await control("backend_start");
  await expect.poll(async () => (await api(page, `/operations/chat/${id}`)).data?.can_retry,
    { timeout: 110_000, intervals: [1000, 2000] }).toBe(true);
  const retried = await api(page, "/chat", payload);
  expect(retried.status).toBe(200);
  expect(retried.data.need_human).toBe(true);
  const sql = await control("inspect", { request_id: id });
  expect(sql.messages.filter((m: any) => m.role === "user")).toHaveLength(1);
  expect(sql.messages.filter((m: any) => m.role === "assistant")).toHaveLength(1);
  expect(sql.tickets).toBe(1);
});

test("真实 Redis 停止期间已提交结果仍可查，恢复后可继续处理", async ({ page }) => {
  await login(page);
  const id = randomUUID();
  const payload = { request_id: id, conversation_id: "conv_a2", message: "Redis 故障期间的已提交消息" };
  // a2 自己的会话，真实登录切换而不绕过身份依赖。
  await login(page, "a2");
  expect((await api(page, "/chat", payload)).status).toBe(200);
  await control("redis_stop");
  try {
    expect((await api(page, `/operations/chat/${id}`)).data.operation_status).toBe("succeeded");
    const next = await api(page, "/chat", { ...payload, request_id: randomUUID(), message: "Redis 停止时的新消息" });
    expect(next.status).toBe(200);
    expect(next.data.operation_status).toBe("succeeded");
  } finally { await control("redis_start"); }
  expect((await api(page, `/conversations/conv_a2`)).data.messages.length).toBeGreaterThan(0);
});

test("模型服务故障不会返回未提交的成功承诺", async ({ page }) => {
  await login(page);
  await control("model", { mode: "error" });
  const id = randomUUID();
  const response = await api(page, "/chat", { request_id: id, message: "故障时请协助诊断接口。" });
  expect(response.status).toBe(200);
  const sql = await control("inspect", { request_id: id });
  expect(response.data.need_human).toBe(Boolean(sql.conversation.transferred_to_human));
  expect(Boolean(response.data.ticket_id)).toBe(sql.tickets === 1);
  expect(sql.messages.filter((m: any) => m.role === "assistant")).toHaveLength(1);
});

test("真实 Milvus 不可用时不伪造知识引用", async ({ page }) => {
  await login(page);
  await control("milvus_stop");
  try {
    const id = randomUUID();
    const response = await api(page, "/chat", { request_id: id, message: "签名参数如何配置？准入向量依赖故障。" });
    expect(response.status).toBe(200);
    expect(response.data.citations || []).toHaveLength(0);
    const sql = await control("inspect", { request_id: id });
    expect(Boolean(response.data.ticket_id)).toBe(sql.tickets === 1);
    expect(response.data.need_human).toBe(Boolean(sql.conversation.transferred_to_human));
  } finally { await control("milvus_start"); }
});

test("停服知识更新中途失败，从快照恢复后引用仍有效", async ({ page }) => {
  await login(page);
  const restored = await control("knowledge_recovery");
  expect(restored.restored_chunks).toBeGreaterThan(0);
  expect(Object.keys(restored.sources).length).toBeGreaterThan(0);
  const response = await api(page, "/chat", { request_id: randomUUID(), message: ragQuestion });
  expect(response.status).toBe(200);
  expect(response.data.citations.length).toBeGreaterThan(0);
  expect(response.data.need_human).toBe(false);
});
