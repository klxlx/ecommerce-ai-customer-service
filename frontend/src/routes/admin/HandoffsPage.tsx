import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { listHandoffs } from "../../api/admin";
import { hasAfterSaleEvidence } from "./afterSaleEvidence";

const FILTERS: { key: string; label: string }[] = [
  { key: "", label: "全部" },
  { key: "open", label: "待处理" },
  { key: "in_progress", label: "处理中" },
  { key: "resolved", label: "已解决" },
];

const STATUS_LABEL: Record<string, string> = {
  open: "待处理",
  in_progress: "处理中",
  resolved: "已解决",
};

export function HandoffsPage() {
  const [filter, setFilter] = useState("");
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "handoffs", filter],
    queryFn: () => listHandoffs(filter || undefined),
  });

  return (
    <div>
      <h2 className="admin-title" style={{ marginBottom: 20 }}>
        工单队列
      </h2>
      <div className="table-toolbar">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={`filter-btn ${filter === f.key ? "active" : ""}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {isLoading && (
        <div className="state-box">
          <div className="spinner" />
        </div>
      )}
      {error && <div className="state-box">加载失败：{String(error)}</div>}
      {!isLoading && !error && data?.length === 0 && (
        <div className="state-box">暂无工单</div>
      )}
      {!isLoading && !error && data && data.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>摘要</th>
              <th>状态</th>
              <th>接待人</th>
              <th>用户</th>
              <th>创建时间</th>
            </tr>
          </thead>
          <tbody>
            {data.map((t) => (
              <tr
                key={t.id}
                className="clickable"
                onClick={() => navigate(`/admin/handoffs/${t.id}`)}
              >
                <td data-label="编号">{t.id}</td>
                <td data-label="摘要">
                  <div className="handoff-summary">
                    <span>{t.summary || "（无摘要）"}</span>
                    {hasAfterSaleEvidence(t) && (
                      <span className="after-sale-tag">售后</span>
                    )}
                    {t.kind === "knowledge_gap" && (
                      <span className="knowledge-gap-tag">知识缺口</span>
                    )}
                    {t.kind === "internal" && (
                      <span className="knowledge-gap-tag">内部异常</span>
                    )}
                    {t.category === "return_exchange" && (
                      <span className="return-exchange-tag">退换货</span>
                    )}
                    {t.category === "refund" && (
                      <span className="return-exchange-tag">退款</span>
                    )}
                    {(t.kind === "internal" || t.kind === "knowledge_gap") &&
                      t.business_status && (
                        <span className="knowledge-gap-tag">
                          {t.business_status}
                        </span>
                      )}
                    {t.rated_at && (
                      <span className="after-sale-tag">已评价</span>
                    )}
                  </div>
                </td>
                <td data-label="状态">
                  <span className={`pill pill-${t.status}`}>
                    {t.kind === "internal" && t.status === "resolved"
                      ? "已处理"
                      : STATUS_LABEL[t.status] || t.status}
                  </span>
                </td>
                <td data-label="接待人">
                  {t.kind === "internal" || t.kind === "knowledge_gap"
                    ? "—"
                    : t.assignee_name || "待接入"}
                </td>
                <td data-label="用户">#{t.user_id}</td>
                <td data-label="创建时间">
                  {new Date(t.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
