import { apiFetch } from "./client";

export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface SourceCitation {
  title: string;
  source_uri: string;
  kb_type: string;
  parent_id: number;
  chunk_id: number;
  score: number;
}

export type AfterSaleStatus =
  | "has_records"
  | "no_records"
  | "not_available"
  | "query_error";

export interface AfterSaleRecord {
  id: number;
  service_type: string;
  status: string;
  refund_amount: string | null;
  reason: string;
  created_at: string;
}

export interface AfterSaleContext {
  status: AfterSaleStatus;
  order_no: string;
  order_status: string | null;
  service_type: string;
  records: AfterSaleRecord[];
  handoff_required: boolean;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant" | "admin" | "system";
  content: string;
  intent: string | null;
  sources: SourceCitation[] | null;
  after_sale?: AfterSaleContext | null;
  created_at: string;
}

export interface ChatResponse {
  answer: string;
  intent: string | null;
  sources: SourceCitation[];
  trace_id: number | null;
  handoff: { id: number; status: string; reason: string } | null;
  after_sale: AfterSaleContext | null;
  conversation_id: number;
  message_id: number;
}

export async function listConversations(): Promise<Conversation[]> {
  return apiFetch<Conversation[]>("/api/conversations");
}

export async function deleteConversation(id: number): Promise<void> {
  return apiFetch<void>(`/api/conversations/${id}`, {
    method: "DELETE",
  });
}

export async function createConversation(
  title?: string,
): Promise<Conversation> {
  return apiFetch<Conversation>("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export async function listMessages(
  conversationId: number,
): Promise<ChatMessage[]> {
  return apiFetch<ChatMessage[]>(
    `/api/conversations/${conversationId}/messages`,
  );
}

export async function sendMessage(
  conversationId: number,
  content: string,
): Promise<ChatResponse> {
  return apiFetch<ChatResponse>(
    `/api/conversations/${conversationId}/messages`,
    { method: "POST", body: JSON.stringify({ content }) },
  );
}
