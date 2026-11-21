import { Fragment, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import {
  getHandoff,
  getHandoffReturnImage,
  updateHandoffStatus,
} from "../../api/admin";
import type { HandoffReturnEvidence } from "../../api/admin";
import { useAuth } from "../../auth/AuthContext";
import {
  readSafeAfterSaleEvidence,
  type SafeAfterSaleEvidence,
} from "./afterSaleEvidence";

const STATUS_LABEL: Record<string, string> = {
  open: "待处理",
  in_progress: "处理中",
  resolved: "已解决",
};

const SLOT_LABEL: Record<string, string> = {
  order_no: "订单号",
  order_item_id: "订单商品 ID",
  product: "商品",
  product_name: "商品名称",
  sku: "SKU",
  model: "型号",
  color: "颜色",
  after_sale_action: "售后动作",
  service_type: "售后类型",
  last_handoff: "最近一次人工交接",
  query_scope: "查询范围",
  pending_intent: "待补充意图",
  missing_slot: "缺少信息",
  damage_type: "破损类型",
  damage_level: "破损程度",
  reason: "退换原因",
  accessories: "配件情况",
  return_address: "退货地址",
  phone: "联系电话",
  refund_amount: "退款金额",
  refund_method: "退款方式",
  refund_status: "退款状态",
  tracking_no: "物流单号",
  logistics_status: "物流状态",
  ticket_id: "工单号",
  note: "处理说明",
  resolved_at: "处理时间",
};

const SLOT_VALUE_LABEL: Record<string, string> = {
  new_request: "新建售后申请",
  list: "订单列表",
  single: "单笔订单",
  order: "订单查询",
  price_stock: "价格与库存查询",
  product_knowledge: "商品咨询",
  after_sale: "售后咨询",
  refund: "退款",
  return: "退货",
  exchange: "换货",
  complaint: "投诉",
  handoff: "转人工",
  knowledge_gap: "知识缺口",
  true: "是",
  false: "否",
};

function formatSlotDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatSlotValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";

  if (key === "last_handoff" && typeof value === "object" && !Array.isArray(value)) {
    const item = value as Record<string, unknown>;
    const parts: string[] = [];
    if (item.ticket_id !== null && item.ticket_id !== undefined) {
      parts.push(`工单 #${item.ticket_id}`);
    }
    if (item.note) parts.push(String(item.note));
    if (item.resolved_at) parts.push(formatSlotDateTime(String(item.resolved_at)));
    return parts.length > 0 ? parts.join(" · ") : "—";
  }

  if (Array.isArray(value)) {
    return value.length > 0
      ? value.map((item) => formatSlotValue("", item)).join("、")
      : "—";
  }

  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(
        ([childKey, childValue]) =>
          `${SLOT_LABEL[childKey] || childKey}：${formatSlotValue(childKey, childValue)}`,
      )
      .join("；");
  }

  const text = String(value);
  return SLOT_VALUE_LABEL[text] || text;
}

export function HandoffDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const handoffId = Number(id);

  const { data: ticket, isLoading, error } = useQuery({
    queryKey: ["admin", "handoff", handoffId],
    queryFn: () => getHandoff(handoffId),
    enabled: !Number.isNaN(handoffId),
  });

  const updateMutation = useMutation({
    mutationFn: (status: string) => updateHandoffStatus(handoffId, status),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["admin", "handoff", handoffId],
      });
      queryClient.invalidateQueries({ queryKey: ["admin", "handoffs"] });
    },
  });

  if (isLoading) {
    return (
      <div className="state-box">
        <div className="spinner" />
      </div>
    );
  }
  if (error || !ticket) {
    return <div className="state-box">工单不存在或加载失败</div>;
  }

  const afterSaleContext = readSafeAfterSaleEvidence(ticket);
  const returnRequest = ticket.evidence?.return_request;
  const isInternal =
    ticket.kind === "internal" || ticket.kind === "knowledge_gap";
  const isKnowledgeGap = ticket.kind === "knowledge_gap";
  const isToolFailure =
    ticket.kind === "internal" &&
    ticket.evidence?.internal_kind === "tool_failure";
  const canResolveInternal =
    isInternal && ticket.status !== "resolved";
  const canTakeOver =
    !isInternal &&
    (ticket.status === "open" ||
      (ticket.status === "in_progress" && ticket.assignee_id == null));
  const requiresClaim =
    ticket.category === "return_exchange" || ticket.category === "refund";
  const canResolve =
    !isInternal &&
    ticket.status !== "resolved" &&
    (!requiresClaim ||
      (ticket.status === "in_progress" && ticket.assignee_id === user?.id));
  const statusLabel =
    ticket.kind === "internal" && ticket.status === "resolved"
      ? "已处理"
      : STATUS_LABEL[ticket.status] || ticket.status;
  const slotEntries = Object.entries(ticket.slots || {}).filter(
    ([key, value]) => {
      const slotValue = String(value);
      return !(
        (key === "order_no" &&
          afterSaleContext?.order?.order_no === slotValue) ||
        (key === "refund_amount" &&
          afterSaleContext?.threshold?.amount === slotValue)
      );
    },
  );

  return (
    <div>
      <button className="btn btn-ghost" onClick={() => navigate("/admin/handoffs")}>
        <ArrowLeft size={16} /> 返回队列
      </button>
      <div className="detail-panel" style={{ marginTop: 16 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: 12,
          }}
        >
          <div>
            <h2 className="admin-title">工单 #{ticket.id}</h2>
            <span className={`pill pill-${ticket.status}`}>
              {statusLabel}
            </span>
            {ticket.kind === "knowledge_gap" && (
              <span className="knowledge-gap-tag">知识缺口</span>
            )}
            {ticket.kind === "internal" && (
              <span className="knowledge-gap-tag">内部异常</span>
            )}
            {ticket.category === "return_exchange" && (
              <span className="return-exchange-tag">退换货</span>
            )}
            {ticket.category === "refund" && (
              <span className="return-exchange-tag">退款</span>
            )}
            {ticket.business_status && (
              <span className="return-exchange-tag">
                {ticket.business_status}
              </span>
            )}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            {isInternal && canResolveInternal && (
              <button
                className="btn btn-primary"
                title={
                  isKnowledgeGap
                    ? "确认缺失知识已补全"
                    : "确认工具异常已修复"
                }
                disabled={updateMutation.isPending}
                onClick={() => updateMutation.mutate("resolved")}
              >
                {updateMutation.isPending
                  ? "处理中…"
                  : isKnowledgeGap
                    ? "已解决"
                    : "已处理"}
              </button>
            )}
            {!isInternal && (
              <>
                <button
                  className="btn btn-secondary"
                  disabled={!canTakeOver || updateMutation.isPending}
                  onClick={() => updateMutation.mutate("in_progress")}
                >
                  {ticket.status === "in_progress" ? "重新接入" : "标记处理中"}
                </button>
                <button
                  className="btn btn-primary"
                  disabled={!canResolve || updateMutation.isPending}
                  onClick={() => updateMutation.mutate("resolved")}
                >
                  标记已解决
                </button>
              </>
            )}
          </div>
        </div>

        {!isInternal && (
          <div className="conversation-time">
            接待人：
            {ticket.assignee_name ||
              (ticket.status === "resolved" ? "—" : "待接入")}
          </div>
        )}

        {isInternal && canResolveInternal && (
          <div className="conversation-time">
            {isKnowledgeGap
              ? "确认缺失知识已补全后点击「已解决」。"
              : isToolFailure
                ? "确认工具异常已修复后点击「已处理」。"
                : "确认内部异常已处理完后点击「已处理」。"}
          </div>
        )}

        {ticket.resolved_by_name && (
          <div className="conversation-time">
            完成人：{ticket.resolved_by_name}
          </div>
        )}
        {requiresClaim &&
          ticket.status === "in_progress" &&
          ticket.assignee_id != null &&
          ticket.assignee_id !== user?.id && (
            <div className="cs-alert cs-alert-muted">
              该退换货/退款工单由其他客服接入，需由其本人解决。
            </div>
          )}

        {updateMutation.error && (
          <div className="error-text" role="alert">
            状态更新失败，请稍后重试
          </div>
        )}

        <div className="detail-section">
          <h3>需求摘要</h3>
          <div>{ticket.summary || "（无）"}</div>
        </div>

        <div className="detail-section">
          <h3>槽位信息</h3>
          <dl className="kv">
            {slotEntries.map(([k, v]) => (
              <Fragment key={k}>
                <dt>{SLOT_LABEL[k] || k}</dt>
                <dd>{formatSlotValue(k, v)}</dd>
              </Fragment>
            ))}
          </dl>
          {slotEntries.length === 0 && (
            <div className="conversation-time">（无）</div>
          )}
        </div>

        {ticket.resolution_note && (
          <div className="detail-section">
            <h3>人工处理结论</h3>
            <div>{ticket.resolution_note}</div>
          </div>
        )}

        {ticket.rated_at && (
          <div className="detail-section">
            <h3>顾客评价</h3>
            <div>
              {ticket.rating ? `${"★".repeat(ticket.rating)}${"☆".repeat(5 - ticket.rating)}` : "未打分"}
              {ticket.rating_comment && (
                <div className="conversation-time">
                  {ticket.rating_comment}
                </div>
              )}
            </div>
          </div>
        )}

        <AfterSaleDetail context={afterSaleContext} />
        <ReturnDetail request={returnRequest} handoffId={ticket.id} />
      </div>
    </div>
  );
}

function AfterSaleDetail({
  context,
}: {
  context: SafeAfterSaleEvidence | null;
}) {
  return (
    <div className="detail-section">
      <h3>关联订单与售后</h3>
      {!context ? (
        <div className="conversation-time">本工单暂无可展示的售后上下文</div>
      ) : (
        <div className="after-sale-admin">
          {context.order && (
            <dl className="kv">
              {context.order.order_no && (
                <>
                  <dt>订单号</dt>
                  <dd>{context.order.order_no}</dd>
                </>
              )}
              {context.order.status && (
                <>
                  <dt>订单状态</dt>
                  <dd>{context.order.status}</dd>
                </>
              )}
              {context.order.total_amount && (
                <>
                  <dt>订单实付</dt>
                  <dd>{`¥${context.order.total_amount}`}</dd>
                </>
              )}
            </dl>
          )}

          {context.afterSale.length > 0 && (
            <div className="after-sale-admin-list">
              {context.afterSale.map((record, index) => (
                <article className="after-sale-record" key={index}>
                  <div className="after-sale-record-top">
                    <strong>{record.service_type || "售后"}</strong>
                    {record.status && (
                      <span className="after-sale-status">
                        {record.status}
                      </span>
                    )}
                  </div>
                  {record.refund_amount && (
                    <div>
                      退款金额：
                      <strong>{`¥${record.refund_amount}`}</strong>
                    </div>
                  )}
                  {record.reason && (
                    <div>
                      原因：<span>{record.reason}</span>
                    </div>
                  )}
                  {record.created_at && (
                    <div className="after-sale-time">
                      申请时间：{formatAfterSaleTime(record.created_at)}
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}

          {context.threshold && (
            <div className="threshold-fact">
              <strong>
                {context.threshold.exceeded
                  ? "超过人工处理阈值"
                  : "未超过人工处理阈值"}
              </strong>
              {context.threshold.amount && context.threshold.limit && (
                <span>
                  {`金额 ¥${context.threshold.amount}，阈值 ¥${context.threshold.limit}`}
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function formatAfterSaleTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
}

function ReturnDetail({
  request,
  handoffId,
}: {
  request?: HandoffReturnEvidence;
  handoffId: number;
}) {
  const [imageUrl, setImageUrl] = useState("");
  const [imageError, setImageError] = useState("");

  useEffect(() => {
    if (!request?.image_id) return;
    let cancelled = false;
    let objectUrl = "";
    setImageUrl("");
    setImageError("");
    getHandoffReturnImage(handoffId)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setImageUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setImageError("退换货图片加载失败");
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [handoffId, request?.image_id]);

  if (!request) return null;

  return (
    <div className="detail-section">
      <h3>退换货申请</h3>
      <div className="return-evidence">
        {imageUrl && (
          <img
            className="return-evidence-image"
            src={imageUrl}
            alt="顾客上传的退换货商品照片"
          />
        )}
        {imageError && <div className="conversation-time">{imageError}</div>}
        <div className="return-evidence-grid">
          <div>
            <span>订单号</span>
            <strong>{request.order_no || "（无）"}</strong>
          </div>
          <div>
            <span>商品</span>
            <strong>{request.product_name || "（无）"}</strong>
          </div>
          <div>
            <span>SKU / 型号 / 颜色</span>
            <strong>
              {request.sku || "—"} / {request.model || "未维护"} /{" "}
              {request.color || "未维护"}
            </strong>
          </div>
          <div>
            <span>破损类型 / 程度</span>
            <strong>
              {request.damage_type || "—"} / {request.damage_level || "—"}
            </strong>
          </div>
          <div>
            <span>退换原因</span>
            <strong>{request.reason || "—"}</strong>
          </div>
          <div>
            <span>识别置信度</span>
            <strong>
              {request.confidence != null
                ? `${Math.round(request.confidence * 100)}%`
                : "—"}
            </strong>
          </div>
          <div>
            <span>配件情况</span>
            <strong>
              {request.accessories?.length
                ? request.accessories.join("、")
                : "未填写"}
            </strong>
          </div>
          <div>
            <span>联系电话</span>
            <strong>{request.phone || "—"}</strong>
          </div>
        </div>
        <div>
          <span className="conversation-time">顾客退货地址</span>
          <div className="return-evidence-note">
            {request.return_address || "（无）"}
          </div>
        </div>
        {request.detected_objects && request.detected_objects.length > 0 && (
          <div className="return-evidence-note">
            视觉识别：
            {request.detected_objects
              .map((item) => item.label_zh || item.label || "")
              .filter(Boolean)
              .join("、")}
          </div>
        )}
      </div>
    </div>
  );
}
