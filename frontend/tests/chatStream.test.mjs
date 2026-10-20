import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

// 直接运行生产函数的转译结果，不另写一份事件流实现，也不新增测试运行库。
const source = await readFile(new URL("../src/chatStream.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
});
const { chatStream, queryOperation } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

function browser(t, response) {
  t.mock.method(globalThis, "fetch", async () => response);
  const old = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true, value: { getItem: () => "fake-token" },
  });
  t.after(() => {
    if (old) Object.defineProperty(globalThis, "localStorage", old);
    else delete globalThis.localStorage;
  });
}

for (const [status, reason] of [
  [401, "登录已失效"], [403, "无权访问"], [404, "会话不存在"], [500, "暂时不可用"],
]) {
  test(`HTTP ${status} 不消费错误正文或调用成功回调`, async (t) => {
    browser(t, {
      ok: false, status,
      get body() { assert.fail("不能将错误正文作为事件流读取"); },
    });
    const unexpected = () => assert.fail("错误响应不能触发成功回调");
    await assert.rejects(chatStream("probe", "conv_probe", {
      onMeta: unexpected, onToken: unexpected, onDone: unexpected,
    }), new RegExp(reason));
  });
}

test("成功但没有 body 时给出明确错误", async (t) => {
  browser(t, new Response(null, { status: 200 }));
  await assert.rejects(chatStream("probe", null, {}), /未返回内容/);
});

test("正常 UTF-8 分块流保持 meta/token/done 与请求结构", async (t) => {
  const bytes = new TextEncoder().encode(
    'event: meta\ndata: {"conversation_id":"conv_probe"}\n\n'
    + 'event: token\ndata: 中文\n\n'
    + 'event: done\ndata: {"answer":"中文"}\n\n',
  );
  const body = new ReadableStream({
    start(controller) {
      for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
      controller.close();
    },
  });
  browser(t, new Response(body));
  const received = [];
  await chatStream("probe", "conv_probe", {
    onMeta: (v) => received.push(v), onToken: (v) => received.push(v),
    onDone: (v) => received.push(v),
  });
  assert.deepEqual(received, [{ conversation_id: "conv_probe" }, "中文", { answer: "中文" }]);
  assert.equal(body.locked, false);
  const [url, init] = fetch.mock.calls[0].arguments;
  assert.equal(url, "/api/chat");
  assert.equal(init.headers.Authorization, "Bearer fake-token");
  const sent = JSON.parse(init.body);
  assert.match(sent.request_id, /^[0-9a-f-]{36}$/);
  assert.deepEqual({ message: sent.message, conversation_id: sent.conversation_id },
                   { message: "probe", conversation_id: "conv_probe" });
});

test("网络读取失败向上传递且释放 reader", async (t) => {
  const body = new ReadableStream({ start(controller) { controller.error(new Error("disconnected")); } });
  browser(t, new Response(body));
  await assert.rejects(chatStream("probe", null, {}), /disconnected/);
  assert.equal(body.locked, false);
});

test("HTTP 202 保留处理中状态，不作为成功流", async (t) => {
  browser(t, new Response(JSON.stringify({ operation_status: "processing" }), { status: 202 }));
  await assert.rejects(chatStream("probe", null, {}), /正在处理中/);
});

test("缺少 done 的中断不能报告成功", async (t) => {
  browser(t, new Response('event: token\ndata: partial\n\n'));
  await assert.rejects(chatStream("probe", null, {}), /连接已中断/);
});

test("原请求重试沿用传入标识", async (t) => {
  browser(t, new Response('event: done\ndata: {"answer":"ready"}\n\n'));
  const id = "00000000-0000-4000-8000-000000000001";
  await chatStream("probe", null, {}, id);
  assert.equal(JSON.parse(fetch.mock.calls[0].arguments[1].body).request_id, id);
});

for (const [code, rejected] of [["conversation_busy", true], ["request_conflict", false]]) {
  test(`409 ${code} 明确是否接受过原请求`, async (t) => {
    browser(t, new Response(JSON.stringify({ detail: { code } }), { status: 409 }));
    await assert.rejects(chatStream("probe", null, {}),
      (error) => error.rejected === rejected && error.conflict === (code === "request_conflict"));
  });
}

test("逐字节 CRLF、多行 data、注释及空事件保持文本换行", async (t) => {
  const bytes = new TextEncoder().encode(': ping\r\n\r\nevent: token\r\ndata: 第一行\r\ndata: 第二行\r\n\r\n'
    + 'event: done\r\ndata: {"answer":"完成"}\r\n\r\n');
  const body = new ReadableStream({ start(c) { for (const b of bytes) c.enqueue(Uint8Array.of(b)); c.close(); } });
  browser(t, new Response(body));
  const tokens = [];
  await chatStream("probe", null, { onToken: (v) => tokens.push(v) });
  assert.deepEqual(tokens, ["第一行\n第二行"]);
});

test("done 后立刻结束等待，取消仍打开的流且不重复完成", async (t) => {
  let cancelled = false;
  const body = new ReadableStream({
    start(c) { c.enqueue(new TextEncoder().encode('event: done\ndata: {}\n\nevent: done\ndata: {}\n\n')); },
    cancel() { cancelled = true; },
  });
  browser(t, new Response(body));
  let completions = 0;
  await chatStream("probe", null, { onDone: () => completions++ });
  assert.equal(completions, 1);
  assert.equal(cancelled, true);
  assert.equal(body.locked, false);
});

for (const payload of ['{secret-invalid', 'null', '[]', '{"operation_status":"processing"}', '{"request_id":"other"}']) {
  test(`非法完成事件不触发完成回调：${payload.slice(0, 20)}`, async (t) => {
    browser(t, new Response(`event: done\ndata: ${payload}\n\n`));
    await assert.rejects(chatStream("probe", null, { onDone: () => assert.fail("不能完成") }), /响应格式无效/);
  });
}

test("主动中断传递 signal 并释放 reader", async (t) => {
  const controller = new AbortController();
  const body = new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('event: token\ndata: partial\n\n')); } });
  browser(t, new Response(body));
  await assert.rejects(chatStream("probe", null, { onToken: () => controller.abort() }, undefined, controller.signal),
    (error) => error.name === "AbortError");
  assert.equal(fetch.mock.calls[0].arguments[1].signal, controller.signal);
  assert.equal(body.locked, false);
});

test("查询 401 提示重新登录且不清除原请求", async (t) => {
  browser(t, new Response("private", { status: 401 }));
  await assert.rejects(queryOperation("chat", "id"), (e) => /重新登录/.test(e.message) && !e.rejected);
});

test("查询响应属于其他 UUID 时不能接受结果", async (t) => {
  browser(t, new Response(JSON.stringify({ request_id: "other", operation_status: "succeeded" })));
  await assert.rejects(queryOperation("chat", "original"), /暂时无法查询/);
});
