/** 只展示预先定义的用户错误，避免显示响应正文或解析异常中的原文。 */
export class ChatRequestError extends Error {}

/** POST SSE：先检查 HTTP 状态，错误响应不能按成功事件流消费。 */
export async function chatStream(
  message: string,
  conversationId: string | null,
  handlers: {
    onMeta?: (m: any) => void;
    onToken?: (t: string) => void;
    onDone?: (d: any) => void;
  }
) {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${localStorage.getItem("token")}`,
    },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });
  if (!resp.ok) {
    const reason = resp.status === 401 ? "登录已失效，请重新登录"
      : resp.status === 403 ? "无权访问该会话"
      : resp.status === 404 ? "会话不存在" : "对话服务暂时不可用";
    throw new ChatRequestError(reason);
  }
  if (!resp.body) throw new ChatRequestError("对话服务未返回内容");
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let event = "";
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
          else if (event === "done") handlers.onDone?.(JSON.parse(data));
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
