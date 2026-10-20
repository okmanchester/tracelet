import { useEffect, useRef, useState } from "react";
import { Card, Input, Button, Tag, Space, Typography, Avatar, Popconfirm, message as antMsg } from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { chatStream, currentUser, submitFeedback } from "../api";
import { ChatRequestError, queryOperation, requestError } from "../chatStream";
import DiagnosisCard from "../components/DiagnosisCard";
import Highlight from "../components/Highlight";
import { PendingOperation as Pending, messagesAfterFailure, persistPending, restorePending } from "../pendingOperation";

interface Msg {
  role: "user" | "assistant";
  content: string;
  citations?: any[];
  card?: any;
  ticket_id?: string | null;
  need_human?: boolean;
  from_cache?: boolean;
  message_id?: string;
  streaming?: boolean;
  request_id?: string;
  attempt_id?: string;
}

export default function Chat() {
  const user = currentUser();
  const storageKey = `tracelet:pending:${user?.tenant_id}:${user?.user_id}`;
  const [pending, setPendingState] = useState<Pending | null>(() => restorePending(sessionStorage, storageKey));
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const convId = useRef<string | null>(null);
  const generation = useRef(0);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => { generation.current++; active.current?.abort(); active.current = null; }, []);
  const begin = () => {
    const controller = new AbortController();
    active.current = controller;
    const current = ++generation.current;
    setSending(true);
    return { signal: controller.signal, valid: () => current === generation.current };
  };
  const stop = () => {
    generation.current++;
    active.current?.abort();
    active.current = null;
    setSending(false);
    if (pending) setPending({ ...pending, can_retry: false });
    setMsgs((prev) => prev.map((m) => ({ ...m, streaming: false })));
    antMsg.info("已停止等待，请查询处理结果；此操作不会取消服务端处理。");
  };
  const setPending = (value: Pending | null) => {
    setPendingState(value);
    persistPending(sessionStorage, storageKey, value);
  };
  const acceptResult = (data: any) => {
    convId.current = data.conversation_id || convId.current;
    const result: Msg = {
      role: "assistant", content: data.answer || "反馈已记录。", card: data.card,
      citations: data.citations, ticket_id: data.ticket_id, need_human: data.need_human,
      from_cache: data.from_cache, message_id: data.message_id, streaming: false,
      request_id: data.request_id,
    };
    setMsgs((prev) => {
      const index = prev.findIndex((m) => m.role === "assistant" && m.request_id === data.request_id);
      return index < 0 ? [...prev, result] : prev.map((m, i) => i === index ? result : m);
    });
    setPending(null);
  };
  const execute = async (operation: Pending, attemptId?: string) => {
    if (active.current) return;
    const attempt = begin();
    setPending({ ...operation, can_retry: false });
    if (operation.kind === "chat") setMsgs((prev) => {
      const placeholder: Msg = { role: "assistant", content: "", streaming: true, request_id: operation.request_id };
      const index = prev.findIndex((m) => m.role === "assistant" && m.request_id === operation.request_id);
      return index < 0 ? [...prev, placeholder] : prev.map((m, i) => i === index ? placeholder : m);
    });
    try {
      if (operation.kind === "feedback") {
        const result = await submitFeedback({ ...operation.payload, request_id: operation.request_id }, attempt.signal);
        if (!attempt.valid()) return;
        if (result.operation_status !== "succeeded") throw new ChatRequestError("反馈正在处理中，请查询进度");
        acceptResult(result);
      } else {
        await chatStream(operation.payload.message, operation.payload.conversation_id, {
          onMeta: (meta) => { if (attempt.valid()) convId.current = meta.conversation_id; },
          onToken: (token) => { if (attempt.valid()) setMsgs((prev) => {
            return prev.map((m) => m.role === "assistant" && m.request_id === operation.request_id
              ? { ...m, content: m.content + token } : m);
          }); },
          onDone: (result) => { if (attempt.valid()) acceptResult(result); },
        }, operation.request_id, attempt.signal);
      }
    } catch (error) {
      if (!attempt.valid()) return;
      const failure = error instanceof ChatRequestError ? error
        : (error as any)?.response?.status ? requestError((error as any).response.status, (error as any).response.data?.detail)
        : new ChatRequestError("处理结果尚未确认，请查询进度");
      antMsg.error(failure.message);
      if (failure.rejected) setPending(null);
      const rejected = failure.rejected || failure.conflict;
      setMsgs((prev) => messagesAfterFailure(prev, operation.request_id, attemptId, rejected));
    } finally { if (attempt.valid()) { active.current = null; setSending(false); } }
  };
  const send = async () => {
    const text = input.trim();
    if (!text || active.current || sending || (pending && !needsOriginal)) return;
    const attemptId = crypto.randomUUID();
    const requestId = pending?.request_id || crypto.randomUUID();
    setInput("");
    setMsgs((prev) => [...prev, { role: "user", content: text, attempt_id: attemptId, request_id: requestId }]);
    await execute({ kind: "chat", request_id: requestId,
                    payload: { message: text, conversation_id: pending ? pending.payload?.conversation_id ?? null : convId.current } }, attemptId);
  };
  const check = async () => {
    if (!pending || active.current || sending) return;
    const attempt = begin();
    setPending({ ...pending, can_retry: false });
    try {
      const result = await queryOperation(pending.kind, pending.request_id, attempt.signal);
      if (!attempt.valid()) return;
      if (result.operation_status === "succeeded") acceptResult(result);
      else {
        setPending({ ...pending, payload: result.retry_payload || pending.payload,
                     can_retry: !!result.can_retry });
        antMsg.info(result.can_retry ? "本次处理未完成，可以重试原请求" : "请求正在处理中");
      }
    } catch (error) {
      if (!attempt.valid()) return;
      if (error instanceof ChatRequestError && error.missing) setPending({ ...pending, can_retry: true });
      antMsg.error(error instanceof ChatRequestError ? error.message : "暂时无法查询处理结果");
    } finally { if (attempt.valid()) { active.current = null; setSending(false); } }
  };
  const feedback = async (type: string, messageId?: string) => {
    if (!convId.current || pending || sending) return;
    await execute({ kind: "feedback", request_id: crypto.randomUUID(),
      payload: { conversation_id: convId.current, message_id: messageId || null, type } });
  };
  const needsOriginal = !!(pending?.kind === "chat" && pending.can_retry && !pending.payload?.message);

  return (
    <div style={{ maxWidth: 860, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <Typography.Text strong>智能技术支持</Typography.Text>
        <Button size="small" disabled={!!pending || sending} onClick={() => feedback("need_human")}>转人工</Button>
      </div>
      <div style={{ minHeight: "60vh", marginBottom: 16 }}>
        {!msgs.length && <Typography.Paragraph type="secondary">
          描述接口报错、接入或账单问题，AI 将基于可用证据协助处理。
        </Typography.Paragraph>}
        {msgs.map((m, i) => (
          <div key={i} style={{ display: "flex", flexDirection: m.role === "user" ? "row-reverse" : "row",
                               gap: 10, margin: "14px 0", alignItems: "flex-start" }}>
            <Avatar style={{ background: m.role === "user" ? "#1677ff" : "#52c41a" }}>
              {m.role === "user" ? "我" : "AI"}
            </Avatar>
            <Card size="small" style={{ maxWidth: "78%" }}>
              {m.role === "user" ? <Highlight text={m.content} />
                : m.card ? <DiagnosisCard card={m.card} />
                : <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content || (m.streaming ? "思考中…" : "结果待确认")}</ReactMarkdown>}
              {m.role === "assistant" && !m.streaming && <>
                {m.from_cache && <Tag color="gold">缓存命中</Tag>}
                {m.need_human && <Tag color="volcano">已转人工</Tag>}
                {m.ticket_id && <Tag color="blue">工单 {m.ticket_id}</Tag>}
                {m.citations?.map((c: any, j: number) => <Tag key={j}>[{c.index}] {c.doc_title} · {c.section}</Tag>)}
                {m.message_id && <Space style={{ marginTop: 8 }}>
                  <Button size="small" disabled={!!pending || sending} onClick={() => feedback("resolved", m.message_id)}>已解决</Button>
                  <Button size="small" disabled={!!pending || sending} onClick={() => feedback("unresolved", m.message_id)}>没解决</Button>
                  <Button size="small" disabled={!!pending || sending} onClick={() => feedback("need_human", m.message_id)}>需要人工</Button>
                </Space>}
              </>}
            </Card>
          </div>
        ))}
      </div>
      {pending && <Space style={{ marginBottom: 12 }}>
        <Typography.Text type="secondary">有待确认的请求</Typography.Text>
        {sending && <Button onClick={stop}>停止等待</Button>}
        <Button onClick={check} loading={sending}>查询进度</Button>
        <Popconfirm title="放弃等待并开启新对话？"
          description="旧请求仍可能完成，此操作不会取消服务端处理。"
          onConfirm={() => { setPending(null); convId.current = null; setMsgs([]); setInput(""); }}>
          <Button disabled={sending}>开启新对话</Button>
        </Popconfirm>
        {pending.can_retry && pending.payload && !needsOriginal && <Button disabled={sending} onClick={() => execute(pending)}>重试原请求</Button>}
        {needsOriginal && <Typography.Text type="secondary">请重新输入原问题以重试</Typography.Text>}
      </Space>}
      <Space.Compact style={{ width: "100%" }}>
        <Input.TextArea value={input} onChange={(e) => setInput(e.target.value)}
          disabled={!!pending && !needsOriginal} autoSize={{ minRows: 1, maxRows: 4 }}
          onPressEnter={(e) => { e.preventDefault(); send(); }} placeholder="描述你的问题…" />
        <Button type="primary" onClick={send} loading={sending} disabled={!!pending && !needsOriginal}>{needsOriginal ? "重试原请求" : "发送"}</Button>
      </Space.Compact>
    </div>
  );
}
