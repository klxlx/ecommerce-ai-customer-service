import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  FileText,
  Plus,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import {
  deleteKnowledge,
  getKnowledge,
  listKnowledge,
  previewKnowledgeFile,
  upsertKnowledge,
  type KnowledgeFilePreview,
  type KnowledgeDetail,
  type KnowledgeItem,
  type KnowledgeType,
  type KnowledgeUpsertPayload,
  type KnowledgeUpsertResult,
} from "../../api/admin";
import { ApiError } from "../../api/client";

interface FormState {
  sourceKey: string | null;
  title: string;
  kbType: KnowledgeType;
  content: string;
}

interface ImportMeta {
  sourceKind: KnowledgeFilePreview["source_kind"];
  originalFilename: string;
  pageCount: number;
  warnings: string[];
}

interface DetectedMetadata {
  applicability: string;
  effectiveDate: string;
}

const EMPTY_FORM: FormState = {
  sourceKey: null,
  title: "",
  kbType: "product",
  content: "",
};

const MAX_KNOWLEDGE_FILE_BYTES = 30 * 1024 * 1024;

const STATUS_LABEL: Record<string, string> = {
  published: "已发布",
  draft: "草稿",
  retired: "已退役",
  failed: "入库失败",
  not_indexed: "未入库",
};

const INGESTION_LABEL: Record<string, string> = {
  succeeded: "入库成功",
  unchanged: "内容未变化",
  failed: "入库失败",
};

const ORIGIN_LABEL: Record<KnowledgeItem["origin"], string> = {
  manual: "人工录入",
  text: "文本导入",
  word: "Word 导入",
  pdf: "PDF 资料",
  image: "图片 OCR",
  unknown: "其他来源",
};

export function KnowledgePage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState("");
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [result, setResult] = useState<KnowledgeUpsertResult | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [importMeta, setImportMeta] = useState<ImportMeta | null>(null);
  const [detectedMetadata, setDetectedMetadata] =
    useState<DetectedMetadata | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null);

  const {
    data: items = [],
    isLoading,
    error,
  } = useQuery({
    queryKey: ["admin", "knowledge"],
    queryFn: listKnowledge,
  });

  const mutation = useMutation({
    mutationFn: (payload: KnowledgeUpsertPayload) => upsertKnowledge(payload),
    onSuccess: (value) => {
      setResult(value);
      const savedItem = value.item;
      if (savedItem) {
        queryClient.setQueryData<KnowledgeItem[]>(
          ["admin", "knowledge"],
          (current) => {
            if (!current) return current;
            const exists = current.some((item) => item.id === savedItem.id);
            return exists
              ? current.map((item) =>
                  item.id === savedItem.id ? savedItem : item,
                )
              : [savedItem, ...current];
          },
        );
      }
      if (savedItem?.can_edit) {
        setForm((current) => ({
          ...current,
          sourceKey: value.source_key,
        }));
      }
      if (savedItem && value.ingestion.status !== "failed") {
        setDetectedMetadata({
          applicability: savedItem.applicability,
          effectiveDate: savedItem.effective_date || "",
        });
      }
      if (value.ingestion.status !== "failed") {
        setImportMeta(null);
        setSelectedFile(null);
      }
    },
  });

  const previewMutation = useMutation({
    mutationFn: (file: File) => previewKnowledgeFile(file),
    onSuccess: (preview) => {
      setForm({
        sourceKey: preview.source_key,
        title: preview.title,
        kbType: "product",
        content: preview.content,
      });
      setDetectedMetadata({
        applicability: preview.applicability,
        effectiveDate: preview.effective_date || "",
      });
      setImportMeta({
        sourceKind: preview.source_kind,
        originalFilename: preview.original_filename,
        pageCount: preview.page_count,
        warnings: preview.warnings,
      });
      setPreviewError("");
      setFormError("");
      setResult(null);
      mutation.reset();
    },
    onError: (error) => {
      setImportMeta(null);
      setPreviewError(errorMessage(error, "文件转换失败，请检查文件后重试"));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteKnowledge(id),
    onSuccess: (_, id) => {
      queryClient.setQueryData<KnowledgeItem[]>(
        ["admin", "knowledge"],
        (current) => current?.filter((item) => item.id !== id),
      );
      setPendingDeleteId(null);
      setFormError("");
    },
    onError: (error) => {
      setPendingDeleteId(null);
      setFormError(errorMessage(error, "知识删除失败，请稍后重试"));
    },
  });

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setFormError("");
  }

  function resetForm() {
    setForm(EMPTY_FORM);
    setFormError("");
    setResult(null);
    setSelectedFile(null);
    setImportMeta(null);
    setDetectedMetadata(null);
    setPreviewError("");
    setPendingDeleteId(null);
    previewMutation.reset();
    mutation.reset();
  }

  async function editItem(item: KnowledgeItem) {
    if (!item.can_edit) return;
    setLoadingDetail(true);
    setFormError("");
    setResult(null);
    setSelectedFile(null);
    setImportMeta(null);
    setDetectedMetadata(null);
    setPreviewError("");
    previewMutation.reset();
    try {
      const detail = await getKnowledge(item.id);
      setForm(fromDetail(detail));
      setDetectedMetadata({
        applicability: detail.applicability,
        effectiveDate: detail.effective_date || "",
      });
    } catch {
      setFormError("知识详情加载失败，请稍后重试");
    } finally {
      setLoadingDetail(false);
    }
  }

  function selectFile(file: File | null) {
    if (file && file.size > MAX_KNOWLEDGE_FILE_BYTES) {
      setSelectedFile(null);
      setImportMeta(null);
      setDetectedMetadata(null);
      setResult(null);
      setPreviewError("文件不能超过 30 MB");
      return;
    }
    setSelectedFile(file);
    setImportMeta(null);
    setDetectedMetadata(null);
    setPreviewError("");
    setResult(null);
    mutation.reset();
  }

  function convertFile() {
    if (!selectedFile) {
      setPreviewError("请先选择要导入的文件");
      return;
    }
    setPreviewError("");
    previewMutation.mutate(selectedFile);
  }

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError("");
    setResult(null);
    if (!form.title.trim() || !form.content.trim()) {
      setFormError("请填写标题和知识内容");
      return;
    }
    const payload: KnowledgeUpsertPayload = {
      source_key: form.sourceKey,
      title: form.title.trim(),
      kb_type: form.kbType,
      content: form.content.trim(),
    };
    if (importMeta) {
      payload.source_kind = importMeta.sourceKind;
      payload.original_filename = importMeta.originalFilename;
    } else if (!form.sourceKey) {
      payload.source_kind = "manual";
      payload.original_filename = null;
    }
    mutation.mutate(payload);
  }

  return (
    <div>
      <div className="admin-page-heading">
        <div>
          <h2 className="admin-title">知识库</h2>
          <p className="admin-page-description">
            管理商品与政策资料，并跟踪版本发布和入库结果。
          </p>
        </div>
        <button className="btn btn-secondary" type="button" onClick={resetForm}>
          <Plus size={16} />
          新增知识
        </button>
      </div>

      <div className="knowledge-layout">
        <section className="knowledge-list" aria-label="知识列表">
          {isLoading && (
            <div className="state-box">
              <div className="spinner" />
            </div>
          )}
          {error && <div className="state-box">知识列表加载失败</div>}
          {!isLoading && !error && items.length === 0 && (
            <div className="state-box">暂无知识，先添加一条商品或政策资料</div>
          )}
          {!isLoading && !error && items.length > 0 && (
            <table className="data-table knowledge-table">
              <thead>
                <tr>
                  <th>知识</th>
                  <th>类型</th>
                  <th>发布状态</th>
                  <th>入库片段</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td data-label="知识">
                      <div className="knowledge-title">{item.title}</div>
                      <div className="knowledge-source">
                        {item.source_uri}
                      </div>
                    </td>
                    <td data-label="类型">
                      <span className="knowledge-type">
                        {item.kb_type === "product" ? "商品知识" : "政策知识"}
                      </span>
                      <span className="knowledge-origin">
                        <FileText size={13} />
                        {ORIGIN_LABEL[item.origin]}
                      </span>
                    </td>
                    <td data-label="发布状态">
                      <span className={`pill pill-${item.status}`}>
                        {STATUS_LABEL[item.status] || item.status}
                      </span>
                      {item.current_version_number !== null && (
                        <span className="knowledge-version">
                          v{item.current_version_number}
                        </span>
                      )}
                    </td>
                    <td data-label="入库片段">
                      {item.chunk_count} 个片段
                    </td>
                    <td data-label="操作">
                      <div className="knowledge-actions">
                        {item.can_edit ? (
                          <button
                            className="btn btn-ghost"
                            type="button"
                            aria-label={`编辑 ${item.title}`}
                            onClick={() => editItem(item)}
                            disabled={loadingDetail}
                          >
                            编辑
                          </button>
                        ) : (
                          <span className="knowledge-readonly">只读</span>
                        )}
                        {pendingDeleteId === item.id ? (
                          <>
                            <button
                              className="btn btn-danger"
                              type="button"
                              onClick={() => deleteMutation.mutate(item.id)}
                              disabled={deleteMutation.isPending}
                            >
                              确认删除
                            </button>
                            <button
                              className="btn btn-ghost"
                              type="button"
                              onClick={() => setPendingDeleteId(null)}
                              disabled={deleteMutation.isPending}
                            >
                              取消
                            </button>
                          </>
                        ) : (
                          <button
                            className="btn btn-ghost knowledge-delete"
                            type="button"
                            aria-label={`删除 ${item.title}`}
                            onClick={() => setPendingDeleteId(item.id)}
                          >
                            <Trash2 size={14} />
                            删除
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="knowledge-editor" aria-label="知识编辑">
          <div className="knowledge-editor-title">
            <BookOpen size={18} />
            <div>
              <strong>
                {importMeta
                  ? "预览并确认"
                  : form.sourceKey
                    ? "更新知识"
                    : "添加知识"}
              </strong>
              <span>
                {importMeta
                  ? "文件已转为 Markdown，请核对后确认入库"
                  : form.sourceKey
                    ? "保存后生成新版本"
                    : "可直接录入，也可先导入文件"}
              </span>
            </div>
          </div>

          <section className="knowledge-import" aria-label="文件导入">
            <div className="knowledge-import-heading">
              <Upload size={16} />
              <div>
                <label htmlFor="knowledge-file">导入文件</label>
                <span>支持 TXT、Markdown、DOCX、PDF 和图片，最大 30 MB</span>
              </div>
            </div>
            <div className="knowledge-file-row">
              <input
                id="knowledge-file"
                className="input knowledge-file-input"
                type="file"
                accept=".txt,.md,.markdown,.docx,.pdf,.png,.jpg,.jpeg,.webp"
                onChange={(event) =>
                  selectFile(event.target.files?.[0] ?? null)
                }
              />
              <button
                className="btn btn-secondary"
                type="button"
                onClick={convertFile}
                disabled={!selectedFile || previewMutation.isPending}
              >
                {previewMutation.isPending ? (
                  <RefreshCw size={15} className="spin-icon" />
                ) : (
                  <FileText size={15} />
                )}
                转换并预览
              </button>
            </div>
            {previewMutation.isPending && (
              <div className="knowledge-preview-progress" role="status">
                正在提取文字并转换为 Markdown
              </div>
            )}
            {previewError && (
              <div className="error-text" role="alert">
                {previewError}
              </div>
            )}
            {importMeta && (
              <div className="knowledge-preview-note">
                <strong>{importMeta.originalFilename}</strong>
                <span>
                  {ORIGIN_LABEL[importMeta.sourceKind]}
                  {(importMeta.sourceKind === "pdf" ||
                    importMeta.sourceKind === "image") &&
                    ` · ${importMeta.pageCount} 页`}
                </span>
                {importMeta.warnings.map((warning) => (
                  <div className="knowledge-preview-warning" key={warning}>
                    {warning}
                  </div>
                ))}
              </div>
            )}
          </section>

          <form className="knowledge-form" onSubmit={submit}>
            <div className="field">
              <label htmlFor="knowledge-title">标题</label>
              <input
                id="knowledge-title"
                className="input"
                value={form.title}
                onChange={(event) => update("title", event.target.value)}
              />
            </div>

            <fieldset className="field knowledge-type-field">
              <legend>知识类型</legend>
              <label>
                <input
                  type="radio"
                  name="kb-type"
                  checked={form.kbType === "product"}
                  onChange={() => update("kbType", "product")}
                />
                商品知识
              </label>
              <label>
                <input
                  type="radio"
                  name="kb-type"
                  checked={form.kbType === "policy"}
                  onChange={() => update("kbType", "policy")}
                />
                政策知识
              </label>
            </fieldset>

            {detectedMetadata &&
              (detectedMetadata.applicability ||
                detectedMetadata.effectiveDate) && (
                <section
                  className="knowledge-detected-metadata"
                  aria-label="自动识别信息"
                >
                  <strong>自动识别</strong>
                  <div className="knowledge-detected-fields">
                    {detectedMetadata.applicability && (
                      <div>
                        <span>适用范围</span>
                        <p>{detectedMetadata.applicability}</p>
                      </div>
                    )}
                    {detectedMetadata.effectiveDate && (
                      <div>
                        <span>生效日期</span>
                        <p>{detectedMetadata.effectiveDate}</p>
                      </div>
                    )}
                  </div>
                </section>
              )}

            <div className="field">
              <label htmlFor="knowledge-content">知识内容</label>
              <textarea
                id="knowledge-content"
                className="input knowledge-content"
                value={form.content}
                onChange={(event) => update("content", event.target.value)}
                placeholder="使用 Markdown 编写固定知识，不要填写价格、库存、订单或物流实时数据。"
              />
            </div>

            {formError && <div className="error-text">{formError}</div>}
            {mutation.isError && (
              <div className="error-text">保存失败，请稍后重试</div>
            )}
            {mutation.isPending && (
              <div className="ingestion-progress" role="status">
                <RefreshCw size={15} className="spin-icon" />
                正在解析并建立索引
              </div>
            )}
            {result && <IngestionResult result={result} />}

            <div className="knowledge-form-actions">
              <button
                className="btn btn-primary"
                type="submit"
                disabled={mutation.isPending}
              >
                {importMeta ? "确认入库" : "保存并入库"}
              </button>
              {form.sourceKey && (
                <button
                  className="btn btn-ghost"
                  type="button"
                  onClick={resetForm}
                >
                  取消编辑
                </button>
              )}
            </div>
          </form>
        </section>
      </div>
    </div>
  );
}

function IngestionResult({ result }: { result: KnowledgeUpsertResult }) {
  const { ingestion } = result;
  return (
    <div
      className={`ingestion-result ingestion-${ingestion.status}`}
      role="status"
    >
      <strong>{INGESTION_LABEL[ingestion.status]}</strong>
      {ingestion.status === "succeeded" && (
        <div className="ingestion-counts">
          <span>{ingestion.success_count} 条知识已入库</span>
          <span>{ingestion.versions_published} 个版本已发布</span>
          <span>{ingestion.chunks_indexed} 个知识片段</span>
        </div>
      )}
      {ingestion.status === "unchanged" && (
        <span>内容与当前发布版本一致，没有创建重复版本。</span>
      )}
      {ingestion.failure_reason && (
        <span className="ingestion-failure">{ingestion.failure_reason}</span>
      )}
    </div>
  );
}

function fromDetail(detail: KnowledgeDetail): FormState {
  return {
    sourceKey: detail.source_key,
    title: detail.title,
    kbType: detail.kb_type,
    content: detail.content,
  };
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError && error.message ? error.message : fallback;
}
