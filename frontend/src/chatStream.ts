/** 只展示预先定义的用户错误，避免显示响应正文或解析异常中的原文。 */
export class ChatRequestError extends Error {
  constructor(message: string, public rejected = false, public missing = false,
              public conflict = false) { super(message); }
}

export async function queryOperation(kind: string, requestId: string) {
  const resp = await fetch(`/api/operations/${encodeURIComponent(kind)}/${encodeURIComponent(requestId)}`, {
    headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
  });
  if (!resp.ok) throw new ChatRequestError(resp.status === 404
    ? "尚未查到已接收请求，可重试原请求" : "暂时无法查询处理结果", false, resp.status === 404);
  return await resp.json();
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
) {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${localStorage.getItem("token")}`,
    },
    body: JSON.stringify({ request_id: requestId, message, conversation_id: conversationId }),
  });
  if (resp.status === 202) throw new ChatRequestError("请求正在处理中，请查询进度");
  if (!resp.ok) {
    let rejected = [401, 403, 404, 422].includes(resp.status);
    let conflict = false;
    if (resp.status === 409) {
      try {
        const detail = (await resp.json()).detail;
        rejected = detail?.code === "conversation_busy" && detail.request_accepted !== true;
        conflict = detail?.code === "request_conflict";
      }
      catch { /* 无法确认拒绝原因时保留原请求。 */ }
    }
    const reason = resp.status === 401 ? "登录已失效，请重新登录"
      : resp.status === 403 ? "无权访问该会话"
      : resp.status === 404 ? "会话不存在"
      : resp.status === 409 ? "会话正在处理或请求标识冲突，请查询原请求"
      : "对话服务暂时不可用，请查询处理结果后重试原请求";
    throw new ChatRequestError(reason, rejected, false, conflict);
  }
  if (!resp.body) throw new ChatRequestError("对话服务未返回内容");
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let event = "";
  let completed = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() || "";
      for (const line of lines) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) {
          const data = line.slice(5).replace(/^ /, "");
          if (event === "meta") handlers.onMeta?.(JSON.parse(data));
          else if (event === "token") handlers.onToken?.(data);
          else if (event === "done") {
            handlers.onDone?.(JSON.parse(data));
            completed = true;
          }
        }
      }
    }
    if (!completed) throw new ChatRequestError("连接已中断，请查询处理结果");
  } finally {
    reader.releaseLock();
  }
}
