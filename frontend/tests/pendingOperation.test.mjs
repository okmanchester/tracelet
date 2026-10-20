import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/pendingOperation.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
});
const { persistPending, restorePending, messagesAfterFailure } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

function storage() {
  const data = new Map();
  return { getItem: (key) => data.get(key) ?? null,
           setItem: (key, value) => data.set(key, value), removeItem: (key) => data.delete(key) };
}
const id = "00000000-0000-4000-8000-000000000001";

test("刷新保留首轮标识与目标，不保存敏感正文", () => {
  const cache = storage();
  persistPending(cache, "user_a", { request_id: id, kind: "chat", can_retry: true,
    payload: { message: "secret_key=synthetic", conversation_id: null } });
  assert.ok(!cache.getItem("user_a").includes("synthetic"));
  assert.deepEqual(restorePending(cache, "user_a"), {
    request_id: id, kind: "chat", payload: { conversation_id: null },
  });
  assert.equal(restorePending(cache, "user_b"), null);
});

test("反馈刷新可复用原标识和动作", () => {
  const cache = storage();
  const value = { request_id: id, kind: "feedback",
    payload: { conversation_id: "conv_safe", message_id: "msg_safe", type: "need_human" } };
  persistPending(cache, "user_a", value);
  assert.deepEqual(restorePending(cache, "user_a"), value);
  persistPending(cache, "user_a", null);
  assert.equal(restorePending(cache, "user_a"), null);
});

test("损坏或非法的本地状态不阻止新对话", () => {
  const cache = storage();
  for (const value of ["{broken", "null", JSON.stringify({ request_id: "wrong", kind: "chat" }),
    JSON.stringify({ request_id: id, kind: "unknown" })]) {
    cache.setItem("user_a", value);
    assert.equal(restorePending(cache, "user_a"), null);
  }
});

test("冲突只移除本次被拒绝的输入和占位，保留原请求与其他消息", () => {
  const original = { role: "user", content: "original", attempt_id: "original" };
  const other = { role: "assistant", content: "done", request_id: "other" };
  const messages = [original, other,
    { role: "user", content: "changed", attempt_id: "new" },
    { role: "assistant", content: "", request_id: id, streaming: true }];
  assert.deepEqual(messagesAfterFailure(messages, id, "new", true), [original, other]);
  const unknown = messagesAfterFailure(messages, id, "new", false);
  assert.equal(unknown.length, 4);
  assert.equal(unknown[3].streaming, false);
  // 首次网络结果未知，随后重试被明确拒绝，也要移除原输入。
  assert.deepEqual(messagesAfterFailure([
    { role: "user", content: "pending", request_id: id, attempt_id: "original" },
    { role: "assistant", request_id: id, streaming: true }, other,
  ], id, undefined, true), [other]);
});

