import { apiFetch } from "./client";

export type ReturnDamageLevel = "轻微" | "中等" | "严重" | "完全损坏";
export type ReturnDamageType =
  | "屏幕裂"
  | "外壳磕碰"
  | "缺件"
  | "错发"
  | "未知";
export type ReturnReason =
  | "7天无理由"
  | "质量问题"
  | "运输破损"
  | "发错货"
  | "缺件"
  | "其他";

export interface DetectedObject {
  label: string;
  label_zh: string;
  confidence: number;
  box: number[];
}

export interface ReturnAnalysis {
  analysis_id: number;
  image_id: string;
  passed: boolean;
  confidence: number;
  product: string;
  product_class: string;
  model: string;
  color: string;
  accessories: string[];
  damage_type: ReturnDamageType;
  detected_objects: DetectedObject[];
  message: string;
  provider: string;
}

export interface ReturnOrderItem {
  order_item_id: number;
  sku: string;
  product_name: string;
  model: string;
  color: string;
  quantity: number;
  unit_price: string;
}

export interface ReturnOrderContext {
  order_no: string;
  status: string;
  items: ReturnOrderItem[];
}

export interface ReturnSubmitPayload {
  conversation_id: number;
  analysis_id: number;
  order_no: string;
  order_item_id: number;
  damage_level: ReturnDamageLevel;
  damage_type: ReturnDamageType;
  reason: ReturnReason;
  accessories: string[];
  return_address: string;
  phone: string;
  confirmed: boolean;
}

export interface ReturnSubmitResult {
  ticket_id: number;
  duplicate: boolean;
  answer: string;
  conversation_id: number;
  message_id: number;
}

export async function analyzeReturnPhoto(
  conversationId: number,
  file: File,
): Promise<ReturnAnalysis> {
  const body = new FormData();
  body.set("conversation_id", String(conversationId));
  body.set("file", file);
  return apiFetch<ReturnAnalysis>("/api/returns/analyze", {
    method: "POST",
    body,
  });
}

export async function getReturnOrder(
  orderNo: string,
): Promise<ReturnOrderContext> {
  return apiFetch<ReturnOrderContext>(
    `/api/returns/orders/${encodeURIComponent(orderNo)}`,
  );
}

export async function submitReturn(
  payload: ReturnSubmitPayload,
): Promise<ReturnSubmitResult> {
  return apiFetch<ReturnSubmitResult>("/api/returns", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
