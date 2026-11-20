import { apiFetch, ApiError, getToken } from "./client";

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

export interface ChatAction {
  type: string;
  label: string;
  description?: string;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant" | "admin" | "system";
  content: string;
  intent: string | null;
  sources: SourceCitation[] | null;
  actions?: ChatAction[] | null;
  after_sale?: AfterSaleContext | null;
  created_at: string;
}

export interface ChatResponse {
  answer: string;
  intent: string | null;
  sources: SourceCitation[];
  actions?: ChatAction[];
  cached?: boolean;
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

/**
 * Stream one assistant turn over SSE. Resolves with the final persisted
 * response once the server emits the `done` event.
 */
export async function sendMessageStream(
  conversationId: number,
  content: string,
  onToken: (text: string) => void,
  signal?: AbortSignal,
  onReplace?: (text: string) => void,
): Promise<ChatResponse> {
  const token = getToken();
  const headers = new Headers({ "Content-Type": "application/json" });
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const resp = await fetch(
    `/api/conversations/${conversationId}/messages/stream`,
    {
      method: "POST",
      headers,
      body: JSON.stringify({ content }),
      signal,
    },
  );

  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new ApiError(resp.status, data.detail || "请求失败");
  }
  if (!resp.body) {
    throw new ApiError(0, "浏览器不支持流式响应");
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed: ChatResponse | null = null;

  function consumeEvent(block: string): void {
    const lines = block.split("\n");
    let event = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (dataLines.length === 0) return;
    let data: unknown;
    try {
      data = JSON.parse(dataLines.join("\n"));
    } catch {
      return;
    }
    if (event === "token") {
      const text = (data as { text?: string }).text;
      if (text) onToken(text);
    } else if (event === "replace") {
      const text = (data as { text?: string }).text;
      if (text) onReplace?.(text);
    } else if (event === "done") {
      completed = data as ChatResponse;
    } else if (event === "error") {
      throw new ApiError(
        0,
        (data as { detail?: string }).detail || "回复生成失败，请稍后重试",
      );
    }
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      if (block.trim()) consumeEvent(block);
    }
  }

  if (buffer.trim()) consumeEvent(buffer);
  if (completed) return completed;
  throw new ApiError(0, "流式响应中断，请重试");
}
