import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

// 直接运行生产函数的转译结果，不另写一份事件流实现，也不新增测试运行库。
const source = await readFile(new URL("../src/chatStream.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
});
const { chatStream } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

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
  assert.deepEqual(JSON.parse(init.body), { message: "probe", conversation_id: "conv_probe" });
});

test("网络读取失败向上传递且释放 reader", async (t) => {
  const body = new ReadableStream({ start(controller) { controller.error(new Error("disconnected")); } });
  browser(t, new Response(body));
  await assert.rejects(chatStream("probe", null, {}), /disconnected/);
  assert.equal(body.locked, false);
});
