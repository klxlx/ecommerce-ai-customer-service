import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, RotateCcw, Search } from "lucide-react";
import {
  listLogs,
  type AfterSaleStatus,
  type OperationalLogItem,
  type OperationalLogQuery,
  type RetrievalResult,
  type RunStatus,
} from "../../api/admin";

interface FilterForm {
  intent: string;
  toolName: string;
  retrievalResult: "" | RetrievalResult;
  afterSaleStatus: "" | AfterSaleStatus;
  thresholdApplied: "" | "true" | "false";
  thresholdExceeded: "" | "true" | "false";
  hasHandoff: "" | "true" | "false";
  runStatus: "" | RunStatus;
  userId: string;
  conversationId: string;
  startAt: string;
  endAt: string;
}

const EMPTY_FILTERS: FilterForm = {
  intent: "",
  toolName: "",
  retrievalResult: "",
  afterSaleStatus: "",
  thresholdApplied: "",
  thresholdExceeded: "",
  hasHandoff: "",
  runStatus: "",
  userId: "",
  conversationId: "",
  startAt: "",
  endAt: "",
};

const DEFAULT_QUERY: OperationalLogQuery = { limit: 20, offset: 0 };

const INTENT_LABELS: Record<string, string> = {
  knowledge: "知识检索",
  after_sale: "售后查询",
  order: "订单查询",
  product: "商品查询",
  handoff: "人工转接",
};

const RETRIEVAL_LABELS: Record<RetrievalResult, string> = {
  hit: "检索命中",
  empty: "检索为空",
  not_applicable: "无需检索",
};

const AFTER_SALE_LABELS: Record<AfterSaleStatus, string> = {
  has_records: "售后：有记录",
  no_records: "售后：无记录",
  not_available: "售后：不可见",
  query_error: "售后：查询异常",
};

const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  success: "成功",
  handoff: "转人工",
  failed: "失败",
  unknown: "未知",
};

const HANDOFF_REASON_LABELS: Record<string, string> = {
  provider_failure: "模型异常",
  threshold_exceeded: "阈值超限",
  tool_failure: "工具失败",
  user_request: "用户请求",
  clarification_failed: "澄清失败",
  other: "其他原因",
};

export function LogsPage() {
  const [filters, setFilters] = useState<FilterForm>(EMPTY_FILTERS);
  const [query, setQuery] = useState<OperationalLogQuery>(DEFAULT_QUERY);
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "logs", query],
    queryFn: () => listLogs(query),
  });

  function update<K extends keyof FilterForm>(
    key: K,
    value: FilterForm[K],
  ) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next: OperationalLogQuery = { limit: 20, offset: 0 };
    if (filters.intent) next.intent = filters.intent;
    if (filters.toolName) next.tool_name = filters.toolName;
    if (filters.retrievalResult) {
      next.retrieval_result = filters.retrievalResult;
    }
    if (filters.afterSaleStatus) {
      next.after_sale_status = filters.afterSaleStatus;
    }
    if (filters.thresholdApplied) {
      next.threshold_applied = filters.thresholdApplied === "true";
    }
    if (filters.thresholdExceeded) {
      next.threshold_exceeded = filters.thresholdExceeded === "true";
    }
    if (filters.hasHandoff) {
      next.has_handoff = filters.hasHandoff === "true";
    }
    if (filters.runStatus) next.run_status = filters.runStatus;
    if (filters.userId) next.user_id = Number(filters.userId);
    if (filters.conversationId) {
      next.conversation_id = Number(filters.conversationId);
    }
    if (filters.startAt) next.start_at = new Date(filters.startAt).toISOString();
    if (filters.endAt) next.end_at = new Date(filters.endAt).toISOString();
    setQuery(next);
  }

  function reset() {
    setFilters(EMPTY_FILTERS);
    setQuery(DEFAULT_QUERY);
  }

  const limit = data?.limit ?? query.limit ?? 20;
  const offset = data?.offset ?? query.offset ?? 0;
  const total = data?.total ?? 0;
  const start = total === 0 ? 0 : offset + 1;
  const end = total === 0 ? 0 : Math.min(offset + limit, total);

  return (
    <div className="logs-page">
      <div className="admin-page-heading">
        <div>
          <h2 className="admin-title">运行日志</h2>
          <p className="admin-page-description">
            查看意图、工具、检索、售后阈值与人工转接的运行结果。
          </p>
        </div>
      </div>

      <form
        className="logs-filter-panel"
        aria-label="运行日志筛选"
        onSubmit={submit}
      >
        <div className="logs-filter-grid">
          <FilterSelect
            label="意图"
            value={filters.intent}
            onChange={(value) => update("intent", value)}
            options={[
              ["knowledge", "知识检索"],
              ["after_sale", "售后查询"],
              ["order", "订单查询"],
              ["product", "商品查询"],
              ["handoff", "人工转接"],
            ]}
          />
          <FilterSelect
            label="工具调用"
            value={filters.toolName}
            onChange={(value) => update("toolName", value)}
            options={[
              ["knowledge", "knowledge"],
              ["after_sale", "after_sale"],
              ["order", "order"],
              ["catalog", "catalog"],
              ["handoff", "handoff"],
            ]}
          />
          <FilterSelect
            label="检索结果"
            value={filters.retrievalResult}
            onChange={(value) =>
              update("retrievalResult", value as FilterForm["retrievalResult"])
            }
            options={[
              ["hit", "检索命中"],
              ["empty", "检索为空"],
              ["not_applicable", "无需检索"],
            ]}
          />
          <FilterSelect
            label="售后状态"
            value={filters.afterSaleStatus}
            onChange={(value) =>
              update(
                "afterSaleStatus",
                value as FilterForm["afterSaleStatus"],
              )
            }
            options={[
              ["has_records", "有记录"],
              ["no_records", "无记录"],
              ["not_available", "订单不可见"],
              ["query_error", "查询异常"],
            ]}
          />
          <FilterSelect
            label="阈值已应用"
            value={filters.thresholdApplied}
            onChange={(value) =>
              update(
                "thresholdApplied",
                value as FilterForm["thresholdApplied"],
              )
            }
            options={[
              ["true", "是"],
              ["false", "否"],
            ]}
          />
          <FilterSelect
            label="阈值超限"
            value={filters.thresholdExceeded}
            onChange={(value) =>
              update(
                "thresholdExceeded",
                value as FilterForm["thresholdExceeded"],
              )
            }
            options={[
              ["true", "是"],
              ["false", "否"],
            ]}
          />
          <FilterSelect
            label="人工转接"
            value={filters.hasHandoff}
            onChange={(value) =>
              update("hasHandoff", value as FilterForm["hasHandoff"])
            }
            options={[
              ["true", "已转接"],
              ["false", "未转接"],
            ]}
          />
          <FilterSelect
            label="运行状态"
            value={filters.runStatus}
            onChange={(value) =>
              update("runStatus", value as FilterForm["runStatus"])
            }
            options={[
              ["success", "成功"],
              ["handoff", "转人工"],
              ["failed", "失败"],
              ["unknown", "未知"],
            ]}
          />
          <FilterInput
            label="用户 ID"
            type="number"
            min={1}
            value={filters.userId}
            onChange={(value) => update("userId", value)}
          />
          <FilterInput
            label="会话 ID"
            type="number"
            min={1}
            value={filters.conversationId}
            onChange={(value) => update("conversationId", value)}
          />
          <FilterInput
            label="开始时间"
            type="datetime-local"
            value={filters.startAt}
            onChange={(value) => update("startAt", value)}
          />
          <FilterInput
            label="结束时间"
            type="datetime-local"
            value={filters.endAt}
            onChange={(value) => update("endAt", value)}
          />
        </div>
        <div className="logs-filter-actions">
          <button className="btn btn-primary" type="submit">
            <Search size={15} />
            查询
          </button>
          <button
            className="btn btn-secondary"
            type="button"
            onClick={reset}
          >
            <RotateCcw size={15} />
            重置
          </button>
        </div>
      </form>

      {isLoading && (
        <div className="state-box">
          <div className="spinner" />
        </div>
      )}
      {error && <div className="state-box">运行日志加载失败</div>}
      {!isLoading && !error && data?.items.length === 0 && (
        <div className="state-box">没有符合条件的运行日志</div>
      )}
      {!isLoading && !error && data && data.items.length > 0 && (
        <>
          <div className="logs-table-wrap">
            <table className="data-table logs-table">
              <thead>
                <tr>
                  <th>运行</th>
                  <th>时间</th>
                  <th>检索</th>
                  <th>售后与阈值</th>
                  <th>转接</th>
                  <th>状态</th>
                  <th>用户 / 会话</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <LogRow key={item.run_id} item={item} />
                ))}
              </tbody>
            </table>
          </div>
          <div className="logs-pagination">
            <span className="logs-range">
              {start}-{end} / {total}
            </span>
            <div className="logs-page-actions">
              <button
                className="btn btn-secondary"
                type="button"
                aria-label="上一页"
                disabled={offset === 0}
                onClick={() =>
                  setQuery((current) => ({
                    ...current,
                    offset: Math.max(
                      0,
                      (current.offset ?? 0) - (current.limit ?? 20),
                    ),
                  }))
                }
              >
                <ChevronLeft size={16} />
                上一页
              </button>
              <button
                className="btn btn-secondary"
                type="button"
                aria-label="下一页"
                disabled={offset + limit >= total}
                onClick={() =>
                  setQuery((current) => ({
                    ...current,
                    offset: (current.offset ?? 0) + (current.limit ?? 20),
                  }))
                }
              >
                下一页
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: [string, string][];
}) {
  const id = `logs-${label}`;
  return (
    <div className="field logs-filter-field">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        className="input"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">全部</option>
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </select>
    </div>
  );
}

function FilterInput({
  label,
  type,
  min,
  value,
  onChange,
}: {
  label: string;
  type: "number" | "datetime-local";
  min?: number;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = `logs-${label}`;
  return (
    <div className="field logs-filter-field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className="input"
        type={type}
        min={min}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

function LogRow({ item }: { item: OperationalLogItem }) {
  return (
    <tr>
      <td data-label="运行">
        <div className="logs-run-id">#{item.run_id}</div>
        <div className="logs-run-intent">
          {item.intent ? INTENT_LABELS[item.intent] || item.intent : "未识别"}
        </div>
        <div className="logs-tool">{item.tool_name}</div>
      </td>
      <td data-label="时间" className="logs-time">
        {new Date(item.created_at).toLocaleString()}
      </td>
      <td data-label="检索">
        <span className={`pill logs-retrieval-${item.retrieval_result}`}>
          {RETRIEVAL_LABELS[item.retrieval_result]}
        </span>
        <div className="logs-source-count">{item.source_count} 个来源</div>
        {item.sources.length > 0 && (
          <ul className="logs-sources">
            {item.sources.map((source, index) => (
              <li key={`${source.title}-${index}`}>
                {source.title} · {sourcePageLabel(source)}
              </li>
            ))}
          </ul>
        )}
      </td>
      <td data-label="售后与阈值">
        <div>
          {item.after_sale_status
            ? AFTER_SALE_LABELS[item.after_sale_status]
            : "售后：无上下文"}
        </div>
        <ThresholdEvidence item={item} />
      </td>
      <td data-label="转接">
        <LogHandoff item={item} />
      </td>
      <td data-label="状态">
        <span className={`pill logs-status-${item.run_status}`}>
          {RUN_STATUS_LABELS[item.run_status]}
        </span>
      </td>
      <td data-label="用户 / 会话">
        <div>user #{item.user_id}</div>
        <div className="logs-conversation">
          conversation #{item.conversation_id}
        </div>
      </td>
    </tr>
  );
}

function ThresholdEvidence({ item }: { item: OperationalLogItem }) {
  if (!item.threshold_applied) {
    return <span className="logs-muted">未应用阈值</span>;
  }
  return (
    <div className="logs-threshold">
      <span>
        {item.threshold_amount || "--"} / {item.threshold_limit || "--"}
      </span>
      <span
        className={
          item.threshold_exceeded
            ? "logs-threshold-exceeded"
            : "logs-muted"
        }
      >
        {item.threshold_exceeded ? "阈值超限" : "未超限"}
      </span>
    </div>
  );
}

function LogHandoff({ item }: { item: OperationalLogItem }) {
  if (!item.has_handoff) {
    return <span className="logs-muted">未转接</span>;
  }
  return (
    <div className="logs-handoff">
      <span>转人工</span>
      <span className="logs-handoff-reason">
        {item.handoff_reason
          ? HANDOFF_REASON_LABELS[item.handoff_reason] || item.handoff_reason
          : "原因未知"}
      </span>
    </div>
  );
}

function sourcePageLabel({
  page_start,
  page_end,
}: OperationalLogItem["sources"][number]) {
  if (page_start === null && page_end === null) return "页码未知";
  if (page_start !== null && page_end !== null) {
    return page_start === page_end
      ? `第 ${page_start} 页`
      : `第 ${page_start}-${page_end} 页`;
  }
  return `第 ${page_start ?? page_end} 页`;
}
