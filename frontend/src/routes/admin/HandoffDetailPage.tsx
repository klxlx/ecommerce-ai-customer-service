import { Fragment } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { getHandoff, updateHandoffStatus } from "../../api/admin";
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
              disabled={
                ticket.status !== "open" || updateMutation.isPending
              }
              onClick={() => updateMutation.mutate("in_progress")}
            >
              标记处理中
            </button>
            <button
              className="btn btn-primary"
              disabled={
                ticket.status === "resolved" || updateMutation.isPending
              }
              onClick={() => updateMutation.mutate("resolved")}
            >
              标记已解决
            </button>
          </div>
        </div>

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
                <dt>{k}</dt>
                <dd>{String(v)}</dd>
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
