/**
 * @author: 公众号：IT杨秀才
 * @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
 */
import axios from "axios";

const api = axios.create({ baseURL: "/api" });

// 每个请求自动带上本地存储的 JWT
api.interceptors.request.use((cfg) => {
  const token = localStorage.getItem("token");
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});

export interface UserInfo {
  user_id: string;
  username: string;
  display_name: string;
  role: string;
  tenant_id: string;
  tenant_name?: string;
}

export const isInternal = (role: string) => role === "support" || role === "admin";

export async function login(username: string, password: string) {
  const { data } = await api.post("/auth/login", { username, password });
  localStorage.setItem("token", data.access_token);
  localStorage.setItem("user", JSON.stringify(data.user));
  return data.user as UserInfo;
}

export function logout() {
  localStorage.removeItem("token");
  localStorage.removeItem("user");
}

export function currentUser(): UserInfo | null {
  const raw = localStorage.getItem("user");
  return raw ? JSON.parse(raw) : null;
}

export const listConversations = () => api.get("/conversations").then((r) => r.data);
export const getConversation = (id: string) => api.get(`/conversations/${id}`).then((r) => r.data);
export const sendCustomerMessage = (id: string, content: string) => api.post(`/conversations/${id}/messages`, { content }).then((r) => r.data);
export const myTickets = () => api.get("/tickets").then((r) => r.data);
export const submitFeedback = (body: any, signal?: AbortSignal) => api.post("/feedback", body, { signal }).then((r) => r.data);
export const wbTickets = (params: any) => api.get("/workbench/tickets", { params }).then((r) => r.data);
export const wbTicketDetail = (id: string) => api.get(`/workbench/tickets/${id}`).then((r) => r.data);
export const wbUpdateTicket = (id: string, body: any) => api.post(`/workbench/tickets/${id}`, body).then((r) => r.data);
export const wbSuggestReply = (convId: string) => api.get(`/workbench/conversations/${convId}/suggest_reply`).then((r) => r.data);
export const wbReply = (convId: string, content: string) => api.post(`/workbench/conversations/${convId}/reply`, { content }).then((r) => r.data);
export const listDocs = (version?: string) => api.get("/docs", { params: { knowledge_version: version } }).then((r) => r.data);
export const getDoc = (id: string, version?: string) => api.get(`/docs/${encodeURIComponent(id)}`, { params: { knowledge_version: version } }).then((r) => r.data);
export const getTrace = (id: string) => api.get(`/traces/${id}`).then((r) => r.data);
export const getMetrics = () => api.get("/metrics").then((r) => r.data);

export { chatStream } from "./chatStream";

export default api;
