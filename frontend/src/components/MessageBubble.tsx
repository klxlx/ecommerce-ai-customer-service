import { Camera } from "lucide-react";
import type { AfterSaleContext, ChatMessage } from "../api/chat";

export function MessageBubble({
  message,
  onReturnRequest,
}: {
  message: ChatMessage;
  onReturnRequest?: () => void;
}) {
  if (message.role === "system") {
    return <div className="chat-system-notice">{message.content}</div>;
  }
  const isUser = message.role === "user";
  return (
    <div className={`message ${message.role}`}>
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
          <div className="after-sale-guidance">已转接人工客服核实</div>
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
