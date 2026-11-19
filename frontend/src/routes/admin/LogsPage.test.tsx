import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import {
  listLogs,
  type OperationalLogItem,
  type OperationalLogPage,
} from "../../api/admin";
import { LogsPage } from "./LogsPage";

vi.mock("../../api/admin", () => ({
  listLogs: vi.fn(),
}));

const mockedListLogs = listLogs as unknown as ReturnType<typeof vi.fn>;

function logItem(
  overrides: Partial<OperationalLogItem> = {},
): OperationalLogItem {
  return {
    run_id: 101,
    conversation_id: 22,
    user_id: 7,
    intent: "knowledge",
    tool_name: "knowledge",
    retrieval_result: "hit",
    source_count: 1,
    sources: [
      {
        title: "HUAWEI Pura 70 用户指南",
        page_start: 6,
        page_end: 8,
      },
    ],
    after_sale_status: "has_records",
    threshold_applied: true,
    threshold_amount: "620.00",
    threshold_limit: "500.00",
    threshold_exceeded: true,
    has_handoff: true,
    handoff_reason: "threshold_exceeded",
    run_status: "handoff",
    created_at: "2026-11-18T08:30:00Z",
    ...overrides,
  };
}

function page(
  overrides: Partial<OperationalLogPage> = {},
): OperationalLogPage {
  return {
    items: [logItem()],
    total: 1,
    limit: 20,
    offset: 0,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedListLogs.mockResolvedValue(page());
});

describe("LogsPage", () => {
  it("renders safe operational facts and source traceability", async () => {
    renderWithProviders(<LogsPage />);

    expect(
      screen.getByRole("heading", { name: "运行日志" }),
    ).toBeInTheDocument();
    const retrievalBadge = await screen.findByText("检索命中", {
      selector: "span.logs-retrieval-hit",
    });
    expect(retrievalBadge).toBeInTheDocument();
    const row = retrievalBadge.closest("tr");
    if (row === null) {
      throw new Error("retrieval badge is not inside a log row");
    }
    expect(row).toHaveTextContent(
      "HUAWEI Pura 70 用户指南 · 第 6-8 页",
    );
    expect(row).toHaveTextContent("知识检索");
    expect(row).toHaveTextContent("售后：有记录");
    expect(row).toHaveTextContent("620.00 / 500.00");
    expect(
      within(row).getByText("阈值超限", {
        selector: "span.logs-threshold-exceeded",
      }),
    ).toBeInTheDocument();
    expect(
      within(row).getByText("转人工", {
        selector: "span.logs-status-handoff",
      }),
    ).toBeInTheDocument();
    expect(row).toHaveTextContent("user #7");
    expect(row).toHaveTextContent("conversation #22");
    expect(mockedListLogs).toHaveBeenCalledWith({
      limit: 20,
      offset: 0,
    });
  });

  it("submits every operational and time-range filter", async () => {
    renderWithProviders(<LogsPage />);
    await screen.findByText("知识检索");

    await userEvent.selectOptions(screen.getByLabelText("意图"), "knowledge");
    await userEvent.selectOptions(
      screen.getByLabelText("工具调用"),
      "knowledge",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("检索结果"),
      "empty",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("售后状态"),
      "query_error",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("阈值已应用"),
      "false",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("阈值超限"),
      "true",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("人工转接"),
      "false",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("运行状态"),
      "failed",
    );
    await userEvent.type(screen.getByLabelText("用户 ID"), "7");
    await userEvent.type(screen.getByLabelText("会话 ID"), "22");
    fireEvent.change(screen.getByLabelText("开始时间"), {
      target: { value: "2026-11-18T08:00" },
    });
    fireEvent.change(screen.getByLabelText("结束时间"), {
      target: { value: "2026-11-18T09:00" },
    });

    await userEvent.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() =>
      expect(mockedListLogs).toHaveBeenLastCalledWith({
        intent: "knowledge",
        tool_name: "knowledge",
        retrieval_result: "empty",
        after_sale_status: "query_error",
        threshold_applied: false,
        threshold_exceeded: true,
        has_handoff: false,
        run_status: "failed",
        user_id: 7,
        conversation_id: 22,
        start_at: expect.stringContaining("2026-11-18T"),
        end_at: expect.stringContaining("2026-11-18T"),
        limit: 20,
        offset: 0,
      }),
    );
  });

  it("pages through filtered results without dropping the active filters", async () => {
    mockedListLogs.mockImplementation((query: { offset?: number }) =>
      Promise.resolve(
        page({
          items: [logItem({ run_id: 102 })],
          total: 45,
          offset: query.offset ?? 0,
        }),
      ),
    );
    renderWithProviders(<LogsPage />);
    await screen.findByText("知识检索");

    await userEvent.selectOptions(screen.getByLabelText("意图"), "knowledge");
    await userEvent.click(screen.getByRole("button", { name: "查询" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "下一页" }),
    );

    await waitFor(() =>
      expect(mockedListLogs).toHaveBeenLastCalledWith({
        intent: "knowledge",
        limit: 20,
        offset: 20,
      }),
    );
    expect(screen.getByText("21-40 / 45")).toBeInTheDocument();
  });

  it("shows a stable empty state after filtering", async () => {
    mockedListLogs.mockResolvedValue(page({ items: [], total: 0 }));

    renderWithProviders(<LogsPage />);

    expect(
      await screen.findByText("没有符合条件的运行日志"),
    ).toBeInTheDocument();
  });

  it("shows a safe error state without exposing transport details", async () => {
    mockedListLogs.mockRejectedValue(
      new Error("postgresql://secret-connection"),
    );

    renderWithProviders(<LogsPage />);

    expect(await screen.findByText("运行日志加载失败")).toBeInTheDocument();
    expect(
      screen.queryByText(/secret-connection/),
    ).not.toBeInTheDocument();
  });
});
