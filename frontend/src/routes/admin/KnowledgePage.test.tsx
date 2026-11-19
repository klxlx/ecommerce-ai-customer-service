import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import {
  deleteKnowledge,
  getKnowledge,
  listKnowledge,
  previewKnowledgeFile,
  upsertKnowledge,
  type KnowledgeDetail,
  type KnowledgeFilePreview,
  type KnowledgeItem,
  type KnowledgeUpsertResult,
} from "../../api/admin";
import { KnowledgePage } from "./KnowledgePage";

vi.mock("../../api/admin", () => ({
  listKnowledge: vi.fn(),
  getKnowledge: vi.fn(),
  previewKnowledgeFile: vi.fn(),
  upsertKnowledge: vi.fn(),
  deleteKnowledge: vi.fn(),
}));

const mockedListKnowledge =
  listKnowledge as unknown as ReturnType<typeof vi.fn>;
const mockedGetKnowledge =
  getKnowledge as unknown as ReturnType<typeof vi.fn>;
const mockedPreviewKnowledgeFile =
  previewKnowledgeFile as unknown as ReturnType<typeof vi.fn>;
const mockedUpsertKnowledge =
  upsertKnowledge as unknown as ReturnType<typeof vi.fn>;
const mockedDeleteKnowledge =
  deleteKnowledge as unknown as ReturnType<typeof vi.fn>;

function item(overrides: Partial<KnowledgeItem> = {}): KnowledgeItem {
  return {
    id: 11,
    source_key: "manual-shipping",
    title: "发货规则",
    kb_type: "policy",
    source_uri: "manual://manual-shipping",
    applicability: "现货商品",
    effective_date: null,
    origin: "manual",
    can_edit: true,
    status: "published",
    current_version_number: 1,
    checksum: "abc",
    parent_count: 1,
    chunk_count: 2,
    published_at: "2026-11-18T08:00:00Z",
    latest_job: {
      id: 3,
      status: "succeeded",
      parent_count: 1,
      chunk_count: 2,
      failure_reason: "",
      started_at: "2026-11-18T07:59:00Z",
      finished_at: "2026-11-18T08:00:00Z",
    },
    ...overrides,
  };
}

function detail(overrides: Partial<KnowledgeDetail> = {}): KnowledgeDetail {
  return {
    ...item(),
    content: "## 发货时间\n固定工作日 48 小时内安排发货。",
    ...overrides,
  };
}

function result(
  overrides: Partial<KnowledgeUpsertResult> = {},
): KnowledgeUpsertResult {
  return {
    source_key: "manual-shipping",
    ingestion: {
      status: "succeeded",
      success_count: 1,
      documents_seen: 1,
      documents_indexed: 1,
      documents_skipped: 0,
      versions_published: 1,
      chunks_indexed: 2,
      failure_reason: "",
    },
    item: item(),
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedListKnowledge.mockResolvedValue([]);
});

describe("KnowledgePage", () => {
  it("loads knowledge with its publication and ingestion status", async () => {
    mockedListKnowledge.mockResolvedValue([item()]);

    renderWithProviders(<KnowledgePage />, {
      initialEntries: ["/admin/knowledge"],
    });

    expect(
      await screen.findByRole("heading", { name: "知识库" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("发货规则")).toBeInTheDocument();
    expect(screen.getByText("已发布")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(screen.getByText("2 个片段")).toBeInTheDocument();
    expect(screen.getByText("人工录入")).toBeInTheDocument();
  });

  it("shows ingestion progress and the successful publication counts", async () => {
    let resolveUpsert: (value: KnowledgeUpsertResult) => void = () => undefined;
    mockedUpsertKnowledge.mockReturnValue(
      new Promise<KnowledgeUpsertResult>((resolve) => {
        resolveUpsert = resolve;
      }),
    );

    renderWithProviders(<KnowledgePage />, {
      initialEntries: ["/admin/knowledge"],
    });

    await userEvent.type(screen.getByLabelText("标题"), "发货规则");
    await userEvent.click(screen.getByLabelText("政策知识"));
    await userEvent.type(
      screen.getByLabelText("知识内容"),
      "## 发货时间\n固定工作日 48 小时内安排发货。",
    );
    await userEvent.click(screen.getByRole("button", { name: "保存并入库" }));

    expect(
      await screen.findByText("正在解析并建立索引"),
    ).toBeInTheDocument();

    await act(async () => {
      resolveUpsert(result());
    });

    expect(await screen.findByText("入库成功")).toBeInTheDocument();
    expect(screen.getByText("1 条知识已入库")).toBeInTheDocument();
    expect(screen.getByText("1 个版本已发布")).toBeInTheDocument();
    expect(screen.getByText("2 个知识片段")).toBeInTheDocument();
    expect(mockedUpsertKnowledge).toHaveBeenCalledWith({
      source_key: null,
      title: "发货规则",
      kb_type: "policy",
      content: "## 发货时间\n固定工作日 48 小时内安排发货。",
      source_kind: "manual",
      original_filename: null,
    });
  });

  it("previews an uploaded file as editable Markdown before confirmation", async () => {
    const file = new File(
      ["# 常见问题\n\n原始说明。"],
      "faq.md",
      { type: "text/markdown" },
    );
    const preview: KnowledgeFilePreview = {
      source_key: "file-faq-sha256",
      title: "faq",
      source_kind: "text",
      original_filename: "faq.md",
      content: "# 常见问题\n\n原始说明。",
      page_count: 1,
      warnings: ["已使用 OCR 识别图片文字，请核对内容。"],
      applicability: "官方渠道购买的商品",
      effective_date: "2026-11-20",
    };
    mockedPreviewKnowledgeFile.mockResolvedValue(preview);
    mockedUpsertKnowledge.mockResolvedValue(
      result({
        source_key: "faq",
        item: item({
          source_key: "faq",
          title: "faq",
          origin: "text",
          source_uri: "faq.md",
        }),
      }),
    );

    renderWithProviders(<KnowledgePage />, {
      initialEntries: ["/admin/knowledge"],
    });

    await userEvent.upload(screen.getByLabelText("导入文件"), file);
    await userEvent.click(
      screen.getByRole("button", { name: "转换并预览" }),
    );

    expect(mockedPreviewKnowledgeFile).toHaveBeenCalledWith(file);
    expect(await screen.findByLabelText("标题")).toHaveValue("faq");
    const contentInput = screen.getByLabelText("知识内容");
    expect(contentInput).toHaveValue("# 常见问题\n\n原始说明。");
    expect(
      screen.getByText("已使用 OCR 识别图片文字，请核对内容。"),
    ).toBeInTheDocument();
    expect(screen.getByText("自动识别")).toBeInTheDocument();
    expect(screen.getByText("官方渠道购买的商品")).toBeInTheDocument();
    expect(screen.getByText("2026-11-20")).toBeInTheDocument();
    expect(
      screen.queryByLabelText("适用范围（可选）"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("生效日期（可选）"),
    ).not.toBeInTheDocument();
    expect(mockedUpsertKnowledge).not.toHaveBeenCalled();

    await userEvent.type(
      contentInput,
      "\n\n人工补充：以商品页公示为准。",
    );
    await userEvent.click(screen.getByRole("button", { name: "确认入库" }));

    expect(mockedUpsertKnowledge).toHaveBeenCalledWith({
      source_key: "file-faq-sha256",
      title: "faq",
      kb_type: "product",
      content:
        "# 常见问题\n\n原始说明。\n\n人工补充：以商品页公示为准。",
      source_kind: "text",
      original_filename: "faq.md",
    });
  });

  it("hides automatic metadata when a file has no supported fields", async () => {
    const file = new File(["## 安装说明"], "guide.md", {
      type: "text/markdown",
    });
    mockedPreviewKnowledgeFile.mockResolvedValue({
      source_key: "file-guide-sha256",
      title: "guide",
      source_kind: "text",
      original_filename: "guide.md",
      content: "## 安装说明\n请先连接官方电源。",
      applicability: "",
      effective_date: null,
      page_count: 0,
      warnings: [],
    } satisfies KnowledgeFilePreview);

    renderWithProviders(<KnowledgePage />, {
      initialEntries: ["/admin/knowledge"],
    });

    await userEvent.upload(screen.getByLabelText("导入文件"), file);
    await userEvent.click(
      screen.getByRole("button", { name: "转换并预览" }),
    );

    expect(await screen.findByLabelText("标题")).toHaveValue("guide");
    expect(screen.queryByText("自动识别")).not.toBeInTheDocument();
  });

  it("shows a safe ingestion failure reason", async () => {
    mockedUpsertKnowledge.mockResolvedValue(
      result({
        ingestion: {
          status: "failed",
          success_count: 0,
          documents_seen: 1,
          documents_indexed: 0,
          documents_skipped: 0,
          versions_published: 0,
          chunks_indexed: 0,
          failure_reason:
            "knowledge content declares a dynamic field (price/stock/order/logistics)",
        },
        item: item({ status: "failed", current_version_number: null }),
      }),
    );

    renderWithProviders(<KnowledgePage />, {
      initialEntries: ["/admin/knowledge"],
    });

    await userEvent.type(screen.getByLabelText("标题"), "价格说明");
    await userEvent.type(screen.getByLabelText("知识内容"), "价格：99 元");
    await userEvent.click(screen.getByRole("button", { name: "保存并入库" }));

    const resultStatus = await screen.findByRole("status");
    expect(within(resultStatus).getByText("入库失败")).toBeInTheDocument();
    expect(
      screen.getByText(
        "knowledge content declares a dynamic field (price/stock/order/logistics)",
      ),
    ).toBeInTheDocument();
  });

  it("shows a failed ingestion result when no knowledge item was created", async () => {
    mockedUpsertKnowledge.mockResolvedValue(
      result({
        ingestion: {
          status: "failed",
          success_count: 0,
          documents_seen: 1,
          documents_indexed: 0,
          documents_skipped: 0,
          versions_published: 0,
          chunks_indexed: 0,
          failure_reason:
            "knowledge content declares a dynamic field (price/stock/order/logistics)",
        },
        item: null,
      }),
    );

    renderWithProviders(<KnowledgePage />, {
      initialEntries: ["/admin/knowledge"],
    });

    await userEvent.type(screen.getByLabelText("标题"), "价格说明");
    await userEvent.type(screen.getByLabelText("知识内容"), "价格：99 元");
    await userEvent.click(screen.getByRole("button", { name: "保存并入库" }));

    const resultStatus = await screen.findByRole("status");
    expect(within(resultStatus).getByText("入库失败")).toBeInTheDocument();
    expect(
      screen.getByText(
        "knowledge content declares a dynamic field (price/stock/order/logistics)",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("保存失败，请稍后重试"),
    ).not.toBeInTheDocument();
  });

  it("opens manual knowledge and updates it through the same source key", async () => {
    mockedListKnowledge.mockResolvedValue([item()]);
    mockedGetKnowledge.mockResolvedValue(detail());
    mockedUpsertKnowledge.mockResolvedValue(
      result({
        item: item({
          title: "发货规则（新版）",
          current_version_number: 2,
        }),
      }),
    );

    renderWithProviders(<KnowledgePage />, {
      initialEntries: ["/admin/knowledge"],
    });

    await userEvent.click(
      await screen.findByRole("button", { name: "编辑 发货规则" }),
    );
    const titleInput = await screen.findByLabelText("标题");
    const contentInput = screen.getByLabelText("知识内容");
    expect(titleInput).toHaveValue("发货规则");
    expect(contentInput).toHaveValue(
      "## 发货时间\n固定工作日 48 小时内安排发货。",
    );
    expect(
      screen.queryByLabelText("适用范围（可选）"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("生效日期（可选）"),
    ).not.toBeInTheDocument();

    await userEvent.clear(titleInput);
    await userEvent.type(titleInput, "发货规则（新版）");
    await userEvent.clear(contentInput);
    await userEvent.type(
      contentInput,
      "## 发货时间\n新版固定工作日 48 小时内安排发货。",
    );
    await userEvent.click(screen.getByRole("button", { name: "保存并入库" }));

    await waitFor(() =>
      expect(mockedUpsertKnowledge).toHaveBeenCalledWith({
        source_key: "manual-shipping",
        title: "发货规则（新版）",
        kb_type: "policy",
        content: "## 发货时间\n新版固定工作日 48 小时内安排发货。",
      }),
    );
    expect(await screen.findByText("v2")).toBeInTheDocument();
  });

  it("keeps registered PDF knowledge read-only in the admin workspace", async () => {
    mockedListKnowledge.mockResolvedValue([
      item({
        id: 20,
        source_key: "hauwei-guide",
        title: "华为手机用户指南",
        kb_type: "product",
        origin: "pdf",
        can_edit: false,
        source_uri: "huawei-guide.pdf",
      }),
    ]);

    renderWithProviders(<KnowledgePage />, {
      initialEntries: ["/admin/knowledge"],
    });

    expect(await screen.findByText("华为手机用户指南")).toBeInTheDocument();
    expect(screen.getByText("PDF 资料")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "编辑 华为手机用户指南" }),
    ).not.toBeInTheDocument();
  });

  it("requires an explicit second action before deleting knowledge", async () => {
    mockedListKnowledge.mockResolvedValue([item()]);
    mockedDeleteKnowledge.mockResolvedValue(undefined);

    renderWithProviders(<KnowledgePage />, {
      initialEntries: ["/admin/knowledge"],
    });

    await userEvent.click(
      await screen.findByRole("button", { name: "删除 发货规则" }),
    );
    expect(mockedDeleteKnowledge).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() =>
      expect(mockedDeleteKnowledge).toHaveBeenCalledWith(11),
    );
  });
});
