import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, MessagesSquare, Send } from "lucide-react";
import {
  getHandoff,
  listHandoffMessages,
  sendHandoffReply,
} from "../../api/admin";
import type { ChatMessage } from "../../api/chat";
import {
  readSafeAfterSaleEvidence,
  type SafeAfterSaleEvidence,
} from "./afterSaleEvidence";

const STATUS_LABEL: Record<string, string> = {
  open: "待处理",
  in_progress: "处理中",
  resolved: "已解决",
};

export function HandoffDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const handoffId = Number(id);
  const [reply, setReply] = useState("");
  const [replyNotice, setReplyNotice] = useState("");

  const { data: ticket, isLoading, error } = useQuery({
    queryKey: ["admin", "handoff", handoffId],
    queryFn: () => getHandoff(handoffId),
    enabled: !Number.isNaN(handoffId),
  });

  const {
    data: messages,
    isLoading: messagesLoading,
    error: messagesError,
  } = useQuery({
    queryKey: ["admin", "handoff", handoffId, "messages"],
    queryFn: () => listHandoffMessages(handoffId),
    enabled: !Number.isNaN(handoffId),
    refetchInterval: 3000,
  });

  const replyMutation = useMutation({
    mutationFn: (content: string) => sendHandoffReply(handoffId, content),
    onMutate: () => {
      setReplyNotice("");
    },
    onSuccess: (message) => {
      queryClient.setQueryData(
        ["admin", "handoff", handoffId, "messages"],
        (current: ChatMessage[] | undefined) => [
          ...(current || []),
          message,
        ],
      );
      queryClient.invalidateQueries({
        queryKey: ["admin", "handoff", handoffId],
      });
      queryClient.invalidateQueries({ queryKey: ["admin", "handoffs"] });
      setReply("");
      setReplyNotice("已发送，会话保持进行中，可在「人工回复」继续跟进");
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
              {STATUS_LABEL[ticket.status] || ticket.status}
            </span>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              className="btn btn-secondary"
              onClick={() => navigate("/admin/live")}
            >
              <MessagesSquare size={16} />
              进入人工回复
            </button>
          </div>
        </div>

        <div className="detail-section">
          <h3>需求摘要</h3>
          <div>{ticket.summary || "（无）"}</div>
        </div>

        <div className="detail-section">
          <h3>槽位信息</h3>
          <dl className="kv">
            {slotEntries.map(([k, v]) => (
              <Fragment key={k}>
                <dt>{k}</dt>
                <dd>{String(v)}</dd>
              </Fragment>
            ))}
          </dl>
          {slotEntries.length === 0 && (
            <div className="conversation-time">（无）</div>
          )}
        </div>

        <div className="detail-section handoff-conversation">
          <h3>会话记录</h3>
          <div className="handoff-message-list" aria-live="polite">
            {messagesLoading && (
              <div className="state-box">
                <div className="spinner" />
              </div>
            )}
            {messagesError && (
              <div className="error-text">会话加载失败，请稍后重试</div>
            )}
            {!messagesLoading &&
              !messagesError &&
              (messages || []).map((message) => (
                <div
                  key={message.id}
                  className={`message ${message.role}`}
                >
                  <div className="message-meta">
                    {message.role === "user"
                      ? "顾客"
                      : message.role === "admin"
                        ? "人工客服"
                        : "智能客服"}
                  </div>
                  <div className="message-bubble">{message.content}</div>
                </div>
              ))}
            {!messagesLoading &&
              !messagesError &&
              (messages || []).length === 0 && (
                <div className="conversation-time">（无）</div>
              )}
          </div>

          <div className="handoff-reply">
            <label htmlFor="handoff-reply">人工回复</label>
            <textarea
              id="handoff-reply"
              className="input"
              rows={3}
              value={reply}
              maxLength={4000}
              disabled={ticket.status === "resolved"}
              onChange={(event) => setReply(event.target.value)}
            />
            {ticket.status === "resolved" && (
              <div className="conversation-time">
                该工单已结束，如需继续沟通请让顾客重新发起会话。
              </div>
            )}
            {replyMutation.error && (
              <div className="error-text" role="alert">
                回复发送失败，请稍后重试
              </div>
            )}
            {replyNotice && (
              <div className="success-text" role="status">
                {replyNotice}
              </div>
            )}
            <button
              className="btn btn-primary"
              disabled={
                !reply.trim() ||
                replyMutation.isPending ||
                ticket.status === "resolved"
              }
              onClick={() => replyMutation.mutate(reply.trim())}
            >
              <Send size={16} />
              发送回复
            </button>
          </div>
        </div>

        <AfterSaleDetail context={afterSaleContext} />
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
