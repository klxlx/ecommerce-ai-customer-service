import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Camera, CheckCircle2, LoaderCircle, X } from "lucide-react";
import { ApiError } from "../../api/client";
import {
  analyzeReturnPhoto,
  getReturnOrder,
  submitReturn,
  type ReturnAnalysis,
  type ReturnDamageLevel,
  type ReturnDamageType,
  type ReturnOrderContext,
  type ReturnReason,
  type ReturnSubmitResult,
} from "../../api/returns";

const DAMAGE_TYPES: ReturnDamageType[] = [
  "屏幕裂",
  "外壳磕碰",
  "缺件",
  "错发",
  "未知",
];
const DAMAGE_LEVELS: ReturnDamageLevel[] = [
  "轻微",
  "中等",
  "严重",
  "完全损坏",
];
const REASONS: ReturnReason[] = [
  "7天无理由",
  "质量问题",
  "运输破损",
  "发错货",
  "缺件",
  "其他",
];

export function ReturnRequestModal({
  conversationId,
  onClose,
  onSubmitted,
}: {
  conversationId: number;
  onClose: () => void;
  onSubmitted: (result: ReturnSubmitResult) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [analysis, setAnalysis] = useState<ReturnAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState("");
  const [orderNo, setOrderNo] = useState("");
  const [order, setOrder] = useState<ReturnOrderContext | null>(null);
  const [orderLoading, setOrderLoading] = useState(false);
  const [itemId, setItemId] = useState<number | null>(null);
  const [damageType, setDamageType] = useState<ReturnDamageType>("未知");
  const [damageLevel, setDamageLevel] = useState<ReturnDamageLevel | "">("");
  const [reason, setReason] = useState<ReturnReason | "">("");
  const [accessories, setAccessories] = useState("");
  const [address, setAddress] = useState("");
  const [phone, setPhone] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState("");

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const selectedItem = useMemo(
    () => order?.items.find((item) => item.order_item_id === itemId) ?? null,
    [order, itemId],
  );
  const canSubmit =
    Boolean(analysis?.passed) &&
    Boolean(order) &&
    Boolean(selectedItem) &&
    damageType !== "未知" &&
    Boolean(damageLevel) &&
    Boolean(reason) &&
    address.trim().length >= 5 &&
    /^1[3-9]\d{9}$/.test(phone) &&
    confirmed &&
    !submitting;

  function submitValidation(): string {
    if (!analysis?.passed) return "请先上传并通过商品照片识别";
    if (!order) return "请先查询订单号";
    if (!selectedItem) return "请选择要退换的商品";
    if (damageType === "未知") return "请选择破损类型";
    if (!damageLevel) return "请选择破损程度";
    if (!reason) return "请选择退换原因";
    if (address.trim().length < 5) return "请填写至少 5 个字的退货地址";
    if (!/^1[3-9]\d{9}$/.test(phone)) return "请填写正确的 11 位手机号";
    if (!confirmed) return "请勾选“照片与商品情况属实”确认";
    return "";
  }

  async function handleAnalyze(nextFile: File | null) {
    if (!nextFile) return;
    setFile(nextFile);
    setAnalysis(null);
    setSuccess("");
    setError("");
    const nextPreview = URL.createObjectURL(nextFile);
    setPreviewUrl((current) => {
      if (current) URL.revokeObjectURL(current);
      return nextPreview;
    });
    setAnalyzing(true);
    try {
      const result = await analyzeReturnPhoto(conversationId, nextFile);
      setAnalysis(result);
      if (
        result.damage_type !== "未知" &&
        DAMAGE_TYPES.includes(result.damage_type)
      ) {
        setDamageType(result.damage_type);
      }
      if (result.passed) {
        setError("");
      } else {
        setError(result.message || "照片未通过识别，请重新拍摄");
      }
    } catch (exc) {
      setError(errorMessage(exc));
    } finally {
      setAnalyzing(false);
    }
  }

  async function handleOrderLookup() {
    const value = orderNo.trim();
    if (!value) {
      setError("请先填写订单号");
      return;
    }
    setOrderLoading(true);
    setError("");
    setOrder(null);
    setItemId(null);
    try {
      const context = await getReturnOrder(value);
      setOrder(context);
      if (context.items.length === 1) {
        setItemId(context.items[0].order_item_id);
      }
    } catch (exc) {
      setError(errorMessage(exc));
    } finally {
      setOrderLoading(false);
    }
  }

  async function handleSubmit() {
    const validation = submitValidation();
    if (validation) {
      setError(validation);
      return;
    }
    if (!analysis || !order || itemId == null) return;
    setSubmitting(true);
    setError("");
    try {
      const result = await submitReturn({
        conversation_id: conversationId,
        analysis_id: analysis.analysis_id,
        order_no: order.order_no,
        order_item_id: itemId,
        damage_level: damageLevel as ReturnDamageLevel,
        damage_type: damageType,
        reason: reason as ReturnReason,
        accessories: accessories
          .split(/[，,、]/)
          .map((item) => item.trim())
          .filter(Boolean),
        return_address: address.trim(),
        phone,
        confirmed,
      });
      setSuccess(result.answer || "已联系管理员，请稍候。");
      onSubmitted(result);
    } catch (exc) {
      setError(errorMessage(exc));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <section
        className="return-modal"
        role="dialog"
        aria-modal="true"
        aria-label="申请退换货"
      >
        <header className="return-modal-header">
          <div>
            <h2>申请退换货</h2>
            <p>上传商品照片，核对识别结果后填写订单和退货信息。</p>
          </div>
          <button
            type="button"
            className="btn btn-ghost"
            aria-label="关闭退换货窗口"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </header>

        {success ? (
          <div className="return-success" role="status">
            <CheckCircle2 size={28} />
            <strong>{success}</strong>
            <span>工单已进入管理端，客服会尽快处理。</span>
          </div>
        ) : (
          <div className="return-modal-body">
            <section className="return-step">
              <div className="return-step-title">
                <span>1</span> 上传商品照片
              </div>
              <div className="return-photo-row">
                {previewUrl ? (
                  <img
                    className="return-photo-preview"
                    src={previewUrl}
                    alt="已上传的商品照片"
                  />
                ) : (
                  <div className="return-photo-placeholder">
                    <Camera size={28} />
                    <span>商品正面、破损处各拍一张更清楚</span>
                  </div>
                )}
                <label className="btn btn-secondary return-file-button">
                  <Camera size={16} />
                  {file ? "重新选择照片" : "选择照片"}
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    hidden
                    onChange={(event) =>
                      handleAnalyze(event.target.files?.[0] ?? null)
                    }
                  />
                </label>
              </div>
              {analyzing && (
                <div className="return-analyzing">
                  <LoaderCircle size={16} className="spin-icon" />
                  正在识别商品照片…
                </div>
              )}
              {analysis && (
                <div
                  className={`return-analysis ${analysis.passed ? "passed" : "blocked"}`}
                >
                  <div className="return-analysis-heading">
                    {analysis.passed ? (
                      <CheckCircle2 size={16} />
                    ) : (
                      <AlertCircle size={16} />
                    )}
                    <strong>
                      {analysis.passed ? "识别通过" : "识别未通过"}
                    </strong>
                    <span>置信度 {Math.round(analysis.confidence * 100)}%</span>
                  </div>
                  <div className="return-analysis-grid">
                    <div>
                      <span>商品</span>
                      <strong>{analysis.product || "未识别"}</strong>
                    </div>
                    <div>
                      <span>型号</span>
                      <strong>{analysis.model || "未识别，按订单带出"}</strong>
                    </div>
                    <div>
                      <span>颜色</span>
                      <strong>{analysis.color || "未识别，按订单带出"}</strong>
                    </div>
                    <div>
                      <span>破损类型</span>
                      <strong>
                        {analysis.damage_type === "未知"
                          ? "未识别，请在下方选择"
                          : analysis.damage_type}
                      </strong>
                    </div>
                  </div>
                  <div className="return-analysis-note">{analysis.message}</div>
                </div>
              )}
            </section>

            {analysis?.passed && (
              <>
                <section className="return-step">
                  <div className="return-step-title">
                    <span>2</span> 填写订单号
                  </div>
                  <div className="return-order-row">
                    <input
                      className="input"
                      value={orderNo}
                      placeholder="例如 ORD-20261110-0015"
                      aria-label="订单号"
                      onChange={(event) => setOrderNo(event.target.value)}
                    />
                    <button
                      type="button"
                      className="btn btn-secondary"
                      onClick={handleOrderLookup}
                      disabled={orderLoading}
                    >
                      {orderLoading ? "查询中…" : "查询订单"}
                    </button>
                  </div>
                  {order && (
                    <div className="return-order-card">
                      <div className="return-order-meta">
                        订单 {order.order_no} · {order.status}
                      </div>
                      {order.items.map((item) => (
                        <label className="return-order-item" key={item.order_item_id}>
                          <input
                            type="radio"
                            name="return-item"
                            checked={itemId === item.order_item_id}
                            onChange={() => setItemId(item.order_item_id)}
                          />
                          <span>
                            <strong>{item.product_name}</strong>
                            <small>
                              SKU {item.sku} · 型号 {item.model || "未维护"} · 颜色{" "}
                              {item.color || "未维护"} · 数量 {item.quantity}
                            </small>
                          </span>
                        </label>
                      ))}
                    </div>
                  )}
                </section>

                {selectedItem && (
                  <section className="return-step">
                    <div className="return-step-title">
                      <span>3</span> 确认退换信息
                    </div>
                    <div className="return-form-grid">
                      <label className="field">
                        <span>破损类型</span>
                        <select
                          className="input"
                          value={damageType}
                          onChange={(event) =>
                            setDamageType(
                              event.target.value as ReturnDamageType,
                            )
                          }
                        >
                          {DAMAGE_TYPES.map((value) => (
                            <option key={value} value={value}>
                              {value === "未知" ? "请选择" : value}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field">
                        <span>破损程度</span>
                        <select
                          className="input"
                          value={damageLevel}
                          onChange={(event) =>
                            setDamageLevel(
                              event.target.value as ReturnDamageLevel,
                            )
                          }
                        >
                          <option value="">请选择</option>
                          {DAMAGE_LEVELS.map((value) => (
                            <option key={value} value={value}>
                              {value}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field">
                        <span>退换原因</span>
                        <select
                          className="input"
                          value={reason}
                          onChange={(event) =>
                            setReason(event.target.value as ReturnReason)
                          }
                        >
                          <option value="">请选择</option>
                          {REASONS.map((value) => (
                            <option key={value} value={value}>
                              {value}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field">
                        <span>配件情况（可选）</span>
                        <input
                          className="input"
                          value={accessories}
                          placeholder="例如：主机、充电器、包装盒"
                          onChange={(event) => setAccessories(event.target.value)}
                        />
                      </label>
                    </div>
                    <label className="field return-address-field">
                      <span>退货地址（顾客取件地址）</span>
                      <textarea
                        className="input"
                        rows={2}
                        value={address}
                        placeholder="请输入详细地址，客服将按此地址安排取件"
                        onChange={(event) => setAddress(event.target.value)}
                      />
                    </label>
                    <label className="field">
                      <span>联系电话</span>
                      <input
                        className="input"
                        inputMode="tel"
                        value={phone}
                        placeholder="请输入 11 位手机号"
                        onChange={(event) => setPhone(event.target.value)}
                      />
                    </label>
                    <label className="return-confirm">
                      <input
                        type="checkbox"
                        checked={confirmed}
                        onChange={(event) => setConfirmed(event.target.checked)}
                      />
                      <span>我确认照片与商品情况属实，并同意客服联系我核实。</span>
                    </label>
                  </section>
                )}
              </>
            )}

            {!canSubmit && !submitting && !error && (
              <div className="conversation-time">
                提交前还需：{submitValidation()}
              </div>
            )}

            {error && (
              <div className="error-text return-error" role="alert">
                {error}
              </div>
            )}

            <footer className="return-modal-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={onClose}
                disabled={submitting}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleSubmit}
                disabled={submitting}
                title={!canSubmit ? submitValidation() : undefined}
              >
                {submitting ? "提交中…" : "提交退换货申请"}
              </button>
            </footer>
          </div>
        )}
      </section>
    </div>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "操作失败，请稍后重试";
}
