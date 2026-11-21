import { useState } from "react";
import { Camera, Star } from "lucide-react";
import { ChatAvatar } from "./ChatAvatar";
import type { AfterSaleContext, ChatAction, ChatMessage } from "../api/chat";
import { submitTicketFeedback } from "../api/chat";

export function MessageBubble({
  message,
  conversationId,
  onReturnRequest,
}: {
  message: ChatMessage;
  conversationId?: number;
  onReturnRequest?: () => void;
}) {
  const feedbackAction = message.actions?.find(
    (action) => action.type === "ticket_feedback",
  );
  if (message.role === "system") {
    return (
      <div className={`chat-system-notice${feedbackAction ? " with-feedback" : ""}`}>
        {message.content}
        {feedbackAction && (
          <TicketFeedbackCard
            action={feedbackAction}
            conversationId={conversationId}
          />
        )}
      </div>
    );
  }
  const isUser = message.role === "user";
  return (
    <div className={`message ${message.role}`}>
      <ChatAvatar role={message.role} />
      <div className="message-stack">
        {message.role === "admin" && (
          <div className="message-meta">人工客服</div>
        )}
        <div className="message-bubble">{message.content}</div>
        {!isUser && onReturnRequest && hasReturnAction(message) && (
          <button
            type="button"
            className="btn btn-secondary return-request-trigger"
            onClick={onReturnRequest}
          >
            <Camera size={16} /> 申请退换货
          </button>
        )}
        {!isUser && message.after_sale && (
          <AfterSalePanel afterSale={message.after_sale} />
        )}
        {!isUser && message.sources && message.sources.length > 0 && (
          <div className="sources" data-testid="message-sources">
            {message.sources.map((s, i) => (
              <span key={i} className="source-chip">
                {s.title}
              </span>
            ))}
          </div>
        )}
        {!isUser && message.intent === "handoff" && (
          <div className="handoff-notice">已转接人工客服</div>
        )}
      </div>
    </div>
  );
}

function TicketFeedbackCard({
  action,
  conversationId,
}: {
  action: ChatAction;
  conversationId?: number;
}) {
  const [rating, setRating] = useState(action.rating ?? 0);
  const [comment, setComment] = useState(action.comment ?? "");
  const [rated, setRated] = useState(Boolean(action.rated));
  const [skipped, setSkipped] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  if (!action.can_rate || skipped) return null;

  if (rated) {
    return (
      <div className="ticket-feedback done">
        <div className="ticket-feedback-title">感谢您的评价</div>
        {rating > 0 && (
          <div className="ticket-feedback-stars" aria-label={`${rating} 星`}>
            {[1, 2, 3, 4, 5].map((value) => (
              <Star
                key={value}
                size={16}
                fill={value <= rating ? "currentColor" : "none"}
              />
            ))}
          </div>
        )}
        {comment.trim() && (
          <div className="ticket-feedback-comment">{comment.trim()}</div>
        )}
      </div>
    );
  }

  const canSubmit = rating > 0 || comment.trim().length > 0;
  const submit = async () => {
    if (!conversationId || !action.ticket_id || !canSubmit || submitting) {
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const result = await submitTicketFeedback(
        conversationId,
        action.ticket_id,
        { rating: rating > 0 ? rating : null, comment: comment.trim() || null },
      );
      setRating(result.rating ?? 0);
      setComment(result.comment ?? "");
      setRated(result.rated);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "评价提交失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="ticket-feedback">
      <div className="ticket-feedback-title">为这次人工服务打个分</div>
      <div className="ticket-feedback-stars" role="group" aria-label="服务评分">
        {[1, 2, 3, 4, 5].map((value) => (
          <button
            key={value}
            type="button"
            className={`ticket-feedback-star${value <= rating ? " active" : ""}`}
            aria-label={`${value} 星`}
            aria-pressed={value === rating}
            onClick={() => setRating(value)}
          >
            <Star size={20} fill={value <= rating ? "currentColor" : "none"} />
          </button>
        ))}
      </div>
      <textarea
        className="ticket-feedback-input"
        value={comment}
        maxLength={50}
        rows={2}
        placeholder="可以补充不超过 50 字的评价（可选）"
        onChange={(event) => setComment(event.target.value)}
      />
      <div className="ticket-feedback-footer">
        <span className="ticket-feedback-count">{comment.length}/50</span>
        <div className="ticket-feedback-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setSkipped(true)}
          >
            跳过
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!canSubmit || submitting || !conversationId}
            onClick={submit}
          >
            {submitting ? "提交中..." : "提交评价"}
          </button>
        </div>
      </div>
      {error && (
        <div className="error-text" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}

function hasReturnAction(message: ChatMessage): boolean {
  return Boolean(
    message.actions?.some((action) => action.type === "return_request"),
  );
}

function AfterSalePanel({ afterSale }: { afterSale: AfterSaleContext }) {
  return (
    <section
      className="after-sale-panel"
      role="region"
      aria-label={`订单 ${afterSale.order_no} 售后信息`}
    >
      <div className="after-sale-heading">
        <strong>订单 {afterSale.order_no}</strong>
        <span>{afterSale.service_type}</span>
      </div>

      {afterSale.status === "has_records" && (
        <>
          {afterSale.order_status && (
            <div className="after-sale-order-state">
              订单状态：{afterSale.order_status}
            </div>
          )}
          <div className="after-sale-records">
            {afterSale.records.map((record) => (
              <article className="after-sale-record" key={record.id}>
                <div className="after-sale-record-top">
                  <strong>{record.service_type}</strong>
                  <span className="after-sale-status">{record.status}</span>
                </div>
                {record.refund_amount && (
                  <div className="after-sale-amount">
                    退款金额：¥{record.refund_amount}
                  </div>
                )}
                <div>原因：{record.reason}</div>
                <div className="after-sale-time">
                  申请时间：{formatAfterSaleTime(record.created_at)}
                </div>
              </article>
            ))}
          </div>
        </>
      )}

      {afterSale.status === "no_records" && (
        <div className="after-sale-empty">
          {afterSale.order_status && (
            <div>订单状态：{afterSale.order_status}</div>
          )}
          <div>暂无售后记录</div>
          <div className="after-sale-guidance">
            可核对订单详情，或转人工客服进一步核实。
          </div>
        </div>
      )}

      {afterSale.status === "not_available" && (
        <div className="after-sale-empty">
          未查询到可展示的订单或售后信息
        </div>
      )}

      {afterSale.status === "query_error" && (
        <div className="after-sale-empty">
          <div>售后查询暂时不可用</div>
          <div className="after-sale-guidance">请稍后重试，或换一种说法再问一次。</div>
        </div>
      )}
    </section>
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
