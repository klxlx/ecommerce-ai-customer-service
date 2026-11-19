import type {
  HandoffAfterSaleEvidence,
  HandoffOrderEvidence,
  HandoffThresholdEvidence,
  HandoffTicket,
} from "../../api/admin";

export interface SafeAfterSaleEvidence {
  order?: HandoffOrderEvidence;
  afterSale: HandoffAfterSaleEvidence[];
  threshold?: HandoffThresholdEvidence;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asOrder(value: unknown): HandoffOrderEvidence | undefined {
  if (!isRecord(value)) return undefined;
  const order: HandoffOrderEvidence = {};
  if (typeof value["order_no"] === "string") {
    order.order_no = value["order_no"];
  }
  if (typeof value["status"] === "string") {
    order.status = value["status"];
  }
  if (typeof value["total_amount"] === "string") {
    order.total_amount = value["total_amount"];
  }
  return Object.keys(order).length > 0 ? order : undefined;
}

function asAfterSale(value: unknown): HandoffAfterSaleEvidence[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!isRecord(item)) return [];
    const record: HandoffAfterSaleEvidence = {};
    if (typeof item["service_type"] === "string") {
      record.service_type = item["service_type"];
    }
    if (typeof item["status"] === "string") {
      record.status = item["status"];
    }
    if (typeof item["refund_amount"] === "string") {
      record.refund_amount = item["refund_amount"];
    } else if (item["refund_amount"] === null) {
      record.refund_amount = null;
    }
    if (typeof item["reason"] === "string") {
      record.reason = item["reason"];
    }
    if (typeof item["created_at"] === "string") {
      record.created_at = item["created_at"];
    }
    return Object.keys(record).length > 0 ? [record] : [];
  });
}

function asThreshold(
  value: unknown,
): HandoffThresholdEvidence | undefined {
  if (!isRecord(value)) return undefined;
  const threshold: HandoffThresholdEvidence = {};
  if (typeof value["applied"] === "boolean") {
    threshold.applied = value["applied"];
  }
  if (typeof value["amount"] === "string") {
    threshold.amount = value["amount"];
  } else if (value["amount"] === null) {
    threshold.amount = null;
  }
  if (typeof value["limit"] === "string") {
    threshold.limit = value["limit"];
  }
  if (typeof value["exceeded"] === "boolean") {
    threshold.exceeded = value["exceeded"];
  }
  return Object.keys(threshold).length > 0 ? threshold : undefined;
}

export function readSafeAfterSaleEvidence(
  ticket: HandoffTicket,
): SafeAfterSaleEvidence | null {
  const tool = isRecord(ticket.evidence) ? ticket.evidence.tool : undefined;
  if (!isRecord(tool)) return null;

  const order = asOrder(tool["order"]);
  const afterSale = asAfterSale(tool["after_sale"]);
  const threshold = asThreshold(tool["threshold"]);
  if (!order && afterSale.length === 0 && !threshold) return null;
  return { order, afterSale, threshold };
}

export function hasAfterSaleEvidence(ticket: HandoffTicket): boolean {
  return readSafeAfterSaleEvidence(ticket) !== null;
}
