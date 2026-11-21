import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { getHandoff, updateHandoffStatus, type HandoffTicket } from "../../api/admin";
import { HandoffDetailPage } from "./HandoffDetailPage";

vi.mock("../../api/admin", () => ({
  getHandoff: vi.fn(),
  updateHandoffStatus: vi.fn(),
}));

const mockedGetHandoff = getHandoff as unknown as ReturnType<typeof vi.fn>;
const mockedUpdateHandoffStatus =
  updateHandoffStatus as unknown as ReturnType<typeof vi.fn>;

function ticket(overrides: Partial<HandoffTicket> = {}): HandoffTicket {
  return {
    id: 7,
    conversation_id: 3,
    user_id: 2,
    status: "open",
    summary: "退款金额超过上限",
    slots: { order_no: "ORD-6002", refund_amount: "500.01" },
    recent_messages: [{ role: "user", content: "我要退款" }],
    evidence: {
      sources: [],
      tool: {
        order: {
          order_no: "ORD-6002",
          status: "已完成",
          total_amount: "620.00",
        },
        after_sale: [
          {
            service_type: "退款",
            status: "审核中",
            refund_amount: "500.01",
            reason: "商品问题",
            created_at: "2026-11-11T10:20:00Z",
          },
        ],
        threshold: {
          applied: true,
          amount: "500.01",
          limit: "500.00",
          exceeded: true,
        },
      },
    },
    created_at: "2026-11-11T10:20:00Z",
    updated_at: "2026-11-11T10:20:00Z",
    ...overrides,
  };
}

describe("HandoffDetailPage after-sale context", () => {
  it("renders only safe order, after-sale and threshold facts", async () => {
    mockedGetHandoff.mockResolvedValue(ticket());

    renderWithProviders(
      <Routes>
        <Route path="/admin/handoffs/:id" element={<HandoffDetailPage />} />
      </Routes>,
      { initialEntries: ["/admin/handoffs/7"] },
    );

    expect(
      await screen.findByRole("heading", { name: "关联订单与售后" }),
    ).toBeInTheDocument();
    expect(screen.getByText("ORD-6002")).toBeInTheDocument();
    expect(screen.getByText("订单状态")).toBeInTheDocument();
    expect(screen.getAllByText("已完成").length).toBeGreaterThan(0);
    expect(screen.getByText("退款")).toBeInTheDocument();
    expect(screen.getAllByText("¥500.01").length).toBeGreaterThan(0);
    expect(screen.getByText("商品问题")).toBeInTheDocument();
    expect(screen.getByText("超过人工处理阈值")).toBeInTheDocument();
    expect(screen.queryByText("检索与工具证据")).not.toBeInTheDocument();
    expect(screen.queryByText(/"sources"/)).not.toBeInTheDocument();
  });

  it("renders a clear empty context for unrelated handoffs", async () => {
    mockedGetHandoff.mockResolvedValue(
      ticket({
        summary: "用户要求人工",
        slots: {},
        evidence: { sources: [], tool: {} },
      }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/admin/handoffs/:id" element={<HandoffDetailPage />} />
      </Routes>,
      { initialEntries: ["/admin/handoffs/7"] },
    );

    expect(
      await screen.findByText("本工单暂无可展示的售后上下文"),
    ).toBeInTheDocument();
  });

  it("updates ticket status without hosting a reply box", async () => {
    mockedGetHandoff.mockResolvedValue(ticket({ status: "open" }));
    mockedUpdateHandoffStatus.mockResolvedValue(
      ticket({ status: "in_progress" }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/admin/handoffs/:id" element={<HandoffDetailPage />} />
      </Routes>,
      { initialEntries: ["/admin/handoffs/7"] },
    );

    const markButton = await screen.findByRole("button", {
      name: "标记处理中",
    });
    await userEvent.click(markButton);

    expect(mockedUpdateHandoffStatus).toHaveBeenCalledWith(7, "in_progress");
    expect(screen.queryByLabelText("人工回复")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "发送回复" }),
    ).not.toBeInTheDocument();
  });

  it("renders ticket slots with Chinese labels and expands handoff objects", async () => {
    mockedGetHandoff.mockResolvedValue(
      ticket({
        slots: {
          order_no: "ORD-20261117-0001",
          product: "声动 Pro 无线蓝牙耳机",
          after_sale_action: "new_request",
          last_handoff: {
            ticket_id: 10,
            note: "已电话沟通并完成处理。",
            resolved_at: "2026-11-21T10:33:13Z",
          },
        },
        evidence: { sources: [], tool: {} },
      }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/admin/handoffs/:id" element={<HandoffDetailPage />} />
      </Routes>,
      { initialEntries: ["/admin/handoffs/7"] },
    );

    expect(await screen.findByText("订单号")).toBeInTheDocument();
    expect(screen.getByText("售后动作")).toBeInTheDocument();
    expect(screen.getByText("新建售后申请")).toBeInTheDocument();
    expect(screen.getByText("最近一次人工交接")).toBeInTheDocument();
    expect(screen.getByText(/工单 #10/)).toHaveTextContent(
      "已电话沟通并完成处理。",
    );
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
  });

  it("marks a knowledge gap as resolved", async () => {
    mockedGetHandoff.mockResolvedValue(
      ticket({
        kind: "knowledge_gap",
        status: "open",
        business_status: "待补全",
      }),
    );
    mockedUpdateHandoffStatus.mockResolvedValue(
      ticket({
        kind: "knowledge_gap",
        status: "resolved",
        business_status: "知识已补全",
      }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/admin/handoffs/:id" element={<HandoffDetailPage />} />
      </Routes>,
      { initialEntries: ["/admin/handoffs/7"] },
    );

    const resolveButton = await screen.findByRole("button", {
      name: "已解决",
    });
    await userEvent.click(resolveButton);
    expect(mockedUpdateHandoffStatus).toHaveBeenCalledWith(7, "resolved");
  });

  it("uses the handled label for an internal tool failure", async () => {
    mockedGetHandoff.mockResolvedValue(
      ticket({
        kind: "internal",
        status: "open",
        business_status: "工具待修复",
        evidence: { sources: [], tool: {}, internal_kind: "tool_failure" },
      }),
    );
    mockedUpdateHandoffStatus.mockResolvedValue(
      ticket({
        kind: "internal",
        status: "resolved",
        business_status: "工具已修复",
        evidence: { sources: [], tool: {}, internal_kind: "tool_failure" },
      }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/admin/handoffs/:id" element={<HandoffDetailPage />} />
      </Routes>,
      { initialEntries: ["/admin/handoffs/7"] },
    );

    const resolveButton = await screen.findByRole("button", {
      name: "已处理",
    });
    await userEvent.click(resolveButton);
    expect(mockedUpdateHandoffStatus).toHaveBeenCalledWith(7, "resolved");
  });

  it("shows the assignee and lets a stale in-progress ticket be re-claimed", async () => {
    mockedGetHandoff.mockResolvedValue(
      ticket({
        kind: "human",
        status: "in_progress",
        assignee_id: null,
        assignee_name: "",
        business_status: "处理中",
      }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/admin/handoffs/:id" element={<HandoffDetailPage />} />
      </Routes>,
      { initialEntries: ["/admin/handoffs/7"] },
    );

    expect(await screen.findByText(/接待人：待接入/)).toBeInTheDocument();
    const takeOver = await screen.findByRole("button", { name: "重新接入" });
    await userEvent.click(takeOver);
    expect(mockedUpdateHandoffStatus).toHaveBeenCalledWith(7, "in_progress");
  });
});
