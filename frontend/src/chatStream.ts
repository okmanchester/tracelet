/** 只展示预先定义的用户错误，避免显示响应正文或解析异常中的原文。 */
export class ChatRequestError extends Error {
  constructor(message: string, public rejected = false, public missing = false,
              public conflict = false) { super(message); }
}

export function requestError(status: number, detail?: any, querying = false) {
  if (querying) return new ChatRequestError(status === 401 ? "登录已失效，请重新登录后查询原请求"
    : status === 404 ? "尚未查到已接收请求，可重试原请求" : "暂时无法查询处理结果",
    false, status === 404);
  const rejected = [401, 403, 404, 422].includes(status)
    || (status === 409 && detail?.code === "conversation_busy" && detail.request_accepted !== true);
  const reason = status === 401 ? "登录已失效，请重新登录"
    : status === 403 ? "无权访问该会话" : status === 404 ? "会话不存在"
    : status === 409 ? "会话正在处理或请求标识冲突，请查询原请求"
    : status === 422 ? "请求参数无效"
    : "对话服务暂时不可用，请查询处理结果后重试原请求";
  return new ChatRequestError(reason, rejected, false, status === 409 && detail?.code === "request_conflict");
}

export async function queryOperation(kind: string, requestId: string, signal?: AbortSignal) {
  const resp = await fetch(`/api/operations/${encodeURIComponent(kind)}/${encodeURIComponent(requestId)}`, {
    headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
    signal,
  });
  if (!resp.ok) throw requestError(resp.status, undefined, true);
  try {
    const data = await resp.json();
    if (!data || typeof data !== "object" || Array.isArray(data) || data.request_id !== requestId
        || !["processing", "failed", "succeeded"].includes(data.operation_status)) {
      throw new Error("invalid operation");
    }
    return data;
  }
  catch { throw new ChatRequestError("暂时无法查询处理结果"); }
}

/** POST SSE：先检查 HTTP 状态，错误响应不能按成功事件流消费。 */
export async function chatStream(
  message: string,
  conversationId: string | null,
  handlers: {
    onMeta?: (m: any) => void;
    onToken?: (t: string) => void;
    onDone?: (d: any) => void;
  },
  requestId: string = crypto.randomUUID(),
  signal?: AbortSignal,
) {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${localStorage.getItem("token")}`,
    },
    body: JSON.stringify({ request_id: requestId, message, conversation_id: conversationId }),
    signal,
  });
  if (resp.status === 202) throw new ChatRequestError("请求正在处理中，请查询进度");
  if (!resp.ok) {
    let detail;
    if (resp.status === 409) {
      try {
        detail = (await resp.json()).detail;
      }
      catch { /* 无法确认拒绝原因时保留原请求。 */ }
    }
    throw requestError(resp.status, detail);
  }
  if (!resp.body) throw new ChatRequestError("对话服务未返回内容");
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let event = "";
  let data: string[] = [];
  let completed = false;
  const line = (value: string) => {
    if (value === "") {
      const type = event;
      const payload = data.join("\n");
      event = "";
      const hasData = data.length > 0;
      data = [];
      if (!hasData) return;
      if (type === "token") handlers.onToken?.(payload);
      else if (type === "meta" || type === "done") {
        let parsed;
        try { parsed = JSON.parse(payload); }
        catch { throw new ChatRequestError("响应格式无效，请查询处理结果"); }
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)
            || (parsed.request_id !== undefined && parsed.request_id !== requestId)
            || (type === "done" && parsed.operation_status !== undefined && parsed.operation_status !== "succeeded")) {
          throw new ChatRequestError("响应格式无效，请查询处理结果");
        }
        if (type === "meta") handlers.onMeta?.(parsed);
        else { completed = true; handlers.onDone?.(parsed); }
      }
      return;
    }
    if (value.startsWith(":")) return;
    const colon = value.indexOf(":");
    const field = colon < 0 ? value : value.slice(0, colon);
    const content = colon < 0 ? "" : value.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = content;
    else if (field === "data") data.push(content);
  };
  try {
    while (true) {
      signal?.throwIfAborted();
      const { done, value } = await reader.read();
      signal?.throwIfAborted();
      buf += done ? decoder.decode() : decoder.decode(value, { stream: true });
      while (true) {
        const index = buf.search(/[\r\n]/);
        if (index < 0 || (!done && buf[index] === "\r" && index === buf.length - 1)) break;
        const value = buf.slice(0, index);
        const width = buf[index] === "\r" && buf[index + 1] === "\n" ? 2 : 1;
        buf = buf.slice(index + width);
        line(value);
        if (completed) return;
      }
      if (done) break;
    }
    if (!completed) throw new ChatRequestError("连接已中断，请查询处理结果");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
