export interface PendingOperation {
  request_id: string;
  kind: "chat" | "feedback";
  payload?: any;
  can_retry?: boolean;
}

export function messagesAfterFailure<T extends {
  role: string; request_id?: string; attempt_id?: string; streaming?: boolean;
}>(messages: T[], requestId: string, attemptId: string | undefined, rejected: boolean): T[] {
  return messages.filter((message) => !rejected || (
    !(attemptId && message.attempt_id === attemptId)
    && !(!attemptId && message.role === "user" && message.request_id === requestId)
    && !(message.role === "assistant" && message.request_id === requestId)
  )).map((message) => message.request_id === requestId ? { ...message, streaming: false } : message);
}

export function restorePending(storage: Storage, key: string): PendingOperation | null {
  try {
    const value = JSON.parse(storage.getItem(key) || "null");
    if (!value || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value.request_id)
        || !["chat", "feedback"].includes(value.kind)) return null;
    return { request_id: value.request_id, kind: value.kind, payload: value.payload };
  } catch { return null; }
}

export function persistPending(storage: Storage, key: string, value: PendingOperation | null) {
  if (!value) { storage.removeItem(key); return; }
  // 只保存业务标识和反馈枚举，原问题由已授权操作查询恢复，绝不保存输入正文。
  const payload = value.payload ? {
    conversation_id: value.payload.conversation_id ?? null,
    ...(value.kind === "feedback" ? {
      message_id: value.payload.message_id ?? null, type: value.payload.type,
    } : {}),
  } : undefined;
  storage.setItem(key, JSON.stringify({ request_id: value.request_id, kind: value.kind, payload }));
}
