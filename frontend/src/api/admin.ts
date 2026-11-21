import { apiFetch, ApiError, getToken } from "./client";
import type { ChatMessage } from "./chat";

export interface HandoffOrderEvidence {
  order_no?: string;
  status?: string;
  total_amount?: string;
}

export interface HandoffAfterSaleEvidence {
  service_type?: string;
  status?: string;
  refund_amount?: string | null;
  reason?: string;
  created_at?: string;
}

export interface HandoffThresholdEvidence {
  applied?: boolean;
  amount?: string | null;
  limit?: string;
  exceeded?: boolean;
}

export interface HandoffToolEvidence {
  order?: HandoffOrderEvidence;
  after_sale?: HandoffAfterSaleEvidence[];
  threshold?: HandoffThresholdEvidence;
}

export interface HandoffReturnEvidence {
  dedupe_key?: string;
  order_no?: string;
  order_item_id?: number;
  sku?: string;
  product_name?: string;
  model?: string;
  color?: string;
  damage_type?: string;
  damage_level?: string;
  reason?: string;
  accessories?: string[];
  return_address?: string;
  phone?: string;
  image_id?: string;
  analysis_id?: number;
  confidence?: number;
  detected_objects?: {
    label?: string;
    label_zh?: string;
    confidence?: number;
  }[];
}

export interface HandoffEvidence {
  sources?: unknown[];
  tool?: HandoffToolEvidence;
  return_request?: HandoffReturnEvidence;
  internal_kind?: "tool_failure" | "provider_failure" | string;
}

export interface HandoffTicket {
  id: number;
  conversation_id: number;
  user_id: number;
  status: "open" | "in_progress" | "resolved";
  kind?: "human" | "knowledge_gap" | "internal";
  category?: "general" | "return_exchange" | "refund";
  summary: string;
  slots: Record<string, unknown>;
  recent_messages: { role: string; content: string }[];
  evidence: HandoffEvidence;
  assignee_id?: number | null;
  assignee_name?: string;
  claimed_at?: string | null;
  resolved_at?: string | null;
  resolved_by_id?: number | null;
  resolved_by_name?: string;
  business_status?: string;
  rating?: number | null;
  rating_comment?: string | null;
  rated_at?: string | null;
  close_reason?: string;
  resolution_note?: string;
  created_at: string;
  updated_at: string;
}

export interface HandoffLiveSession {
  id: number;
  conversation_id: number;
  user_id: number;
  username: string;
  status: "open" | "in_progress";
  kind?: "human" | "knowledge_gap" | "internal";
  category?: "general" | "return_exchange" | "refund";
  assignee_id: number | null;
  assignee_name: string;
  summary: string;
  created_at: string;
  claimed_at: string | null;
  waiting_seconds: number;
  last_message: string;
  last_message_role: string;
  last_message_at: string | null;
  message_count: number;
}

export type KnowledgeType = "product" | "policy";
export type KnowledgeOrigin =
  | "manual"
  | "text"
  | "word"
  | "pdf"
  | "image"
  | "unknown";
export type KnowledgeIngestionStatus = "succeeded" | "failed" | "unchanged";

export interface KnowledgeJob {
  id: number;
  status: string;
  parent_count: number;
  chunk_count: number;
  failure_reason: string;
  started_at: string;
  finished_at: string | null;
}

export interface KnowledgeItem {
  id: number;
  source_key: string;
  title: string;
  kb_type: KnowledgeType;
  source_uri: string;
  applicability: string;
  effective_date: string | null;
  origin: KnowledgeOrigin;
  can_edit: boolean;
  status: string;
  current_version_number: number | null;
  checksum: string;
  parent_count: number;
  chunk_count: number;
  published_at: string | null;
  latest_job: KnowledgeJob | null;
}

export interface KnowledgeDetail extends KnowledgeItem {
  content: string;
}

export interface KnowledgeUpsertPayload {
  source_key: string | null;
  title: string;
  kb_type: KnowledgeType;
  content: string;
  source_kind?: KnowledgeOrigin;
  original_filename?: string | null;
}

export interface KnowledgeFilePreview {
  source_key: string;
  title: string;
  source_kind: KnowledgeOrigin;
  original_filename: string;
  content: string;
  page_count: number;
  warnings: string[];
  applicability: string;
  effective_date: string | null;
}

export interface KnowledgeIngestion {
  status: KnowledgeIngestionStatus;
  success_count: number;
  documents_seen: number;
  documents_indexed: number;
  documents_skipped: number;
  versions_published: number;
  chunks_indexed: number;
  failure_reason: string;
}

export interface KnowledgeUpsertResult {
  source_key: string;
  ingestion: KnowledgeIngestion;
  item: KnowledgeItem | null;
}

export type RetrievalResult = "hit" | "empty" | "not_applicable";
export type AfterSaleStatus =
  | "has_records"
  | "no_records"
  | "not_available"
  | "query_error";
export type RunStatus = "success" | "handoff" | "failed" | "unknown";

export interface OperationalLogSource {
  title: string;
  page_start: number | null;
  page_end: number | null;
}

export interface OperationalLogItem {
  run_id: number;
  conversation_id: number;
  user_id: number;
  intent: string | null;
  tool_name: string;
  retrieval_result: RetrievalResult;
  source_count: number;
  sources: OperationalLogSource[];
  after_sale_status: AfterSaleStatus | null;
  threshold_applied: boolean;
  threshold_amount: string | null;
  threshold_limit: string | null;
  threshold_exceeded: boolean;
  has_handoff: boolean;
  handoff_reason: string | null;
  run_status: RunStatus;
  created_at: string;
}

export interface OperationalLogPage {
  items: OperationalLogItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface OperationalLogQuery {
  intent?: string;
  tool_name?: string;
  retrieval_result?: RetrievalResult;
  after_sale_status?: AfterSaleStatus;
  threshold_applied?: boolean;
  threshold_exceeded?: boolean;
  has_handoff?: boolean;
  run_status?: RunStatus;
  user_id?: number;
  conversation_id?: number;
  start_at?: string;
  end_at?: string;
  limit?: number;
  offset?: number;
}

export async function listHandoffs(
  status?: string,
): Promise<HandoffTicket[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiFetch<HandoffTicket[]>(`/api/admin/handoffs${query}`);
}

export async function listLiveSessions(): Promise<HandoffLiveSession[]> {
  return apiFetch<HandoffLiveSession[]>("/api/admin/handoffs/live");
}

export async function claimHandoff(id: number): Promise<HandoffTicket> {
  return apiFetch<HandoffTicket>(`/api/admin/handoffs/${id}/claim`, {
    method: "POST",
  });
}

export async function releaseHandoff(
  id: number,
  note: string,
): Promise<HandoffTicket> {
  return apiFetch<HandoffTicket>(`/api/admin/handoffs/${id}/release`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });
}

export async function getHandoff(id: number): Promise<HandoffTicket> {
  return apiFetch<HandoffTicket>(`/api/admin/handoffs/${id}`);
}

export async function updateHandoffStatus(
  id: number,
  status: string,
): Promise<HandoffTicket> {
  return apiFetch<HandoffTicket>(`/api/admin/handoffs/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export async function listHandoffMessages(
  id: number,
  afterId?: number,
): Promise<ChatMessage[]> {
  const query = afterId ? `?after_id=${afterId}` : "";
  return apiFetch<ChatMessage[]>(`/api/admin/handoffs/${id}/messages${query}`);
}

export async function sendHandoffReply(
  id: number,
  content: string,
  action?: "return_request",
): Promise<ChatMessage> {
  return apiFetch<ChatMessage>(`/api/admin/handoffs/${id}/messages`, {
    method: "POST",
    body: JSON.stringify({ content, action }),
  });
}

export async function getHandoffReturnImage(id: number): Promise<Blob> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const resp = await fetch(`/api/admin/handoffs/${id}/return-image`, {
    headers,
  });
  if (!resp.ok) {
    throw new ApiError(resp.status, "退换货图片加载失败");
  }
  return resp.blob();
}

export async function listKnowledge(): Promise<KnowledgeItem[]> {
  return apiFetch<KnowledgeItem[]>("/api/admin/knowledge");
}

export async function getKnowledge(id: number): Promise<KnowledgeDetail> {
  return apiFetch<KnowledgeDetail>(`/api/admin/knowledge/${id}`);
}

export async function previewKnowledgeFile(
  file: File,
): Promise<KnowledgeFilePreview> {
  const body = new FormData();
  body.set("file", file);
  return apiFetch<KnowledgeFilePreview>("/api/admin/knowledge/preview", {
    method: "POST",
    body,
  });
}

export async function upsertKnowledge(
  payload: KnowledgeUpsertPayload,
): Promise<KnowledgeUpsertResult> {
  return apiFetch<KnowledgeUpsertResult>("/api/admin/knowledge", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteKnowledge(id: number): Promise<void> {
  return apiFetch<void>(`/api/admin/knowledge/${id}`, {
    method: "DELETE",
  });
}

export async function listLogs(
  query: OperationalLogQuery = {},
): Promise<OperationalLogPage> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const search = params.toString();
  return apiFetch<OperationalLogPage>(
    `/api/admin/logs${search ? `?${search}` : ""}`,
  );
}
