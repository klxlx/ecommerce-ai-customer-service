import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import {
  getHandoff,
  listHandoffMessages,
  sendHandoffReply,
  type HandoffTicket,
} from "../../api/admin";
import { HandoffDetailPage } from "./HandoffDetailPage";

vi.mock("../../api/admin", () => ({
  getHandoff: vi.fn(),
  listHandoffMessages: vi.fn(),
  sendHandoffReply: vi.fn(),
  updateHandoffStatus: vi.fn(),
}));

const mockedGetHandoff = getHandoff as unknown as ReturnType<typeof vi.fn>;
const mockedListHandoffMessages =
  listHandoffMessages as unknown as ReturnType<typeof vi.fn>;
const mockedSendHandoffReply =
  sendHandoffReply as unknown as ReturnType<typeof vi.fn>;

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
    mockedListHandoffMessages.mockResolvedValue([]);

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
    mockedListHandoffMessages.mockResolvedValue([]);

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

  it("sends an admin reply and returns the conversation to the agent", async () => {
    mockedGetHandoff.mockResolvedValue(ticket({ status: "in_progress" }));
    mockedListHandoffMessages.mockResolvedValue([
      {
        id: 1,
        role: "user",
        content: "可以便宜一点吗？",
        intent: null,
        sources: null,
        created_at: "2026-11-11T10:20:00Z",
      },
    ]);
    mockedSendHandoffReply.mockResolvedValue({
      id: 2,
      role: "admin",
      content: "可以为您申请 20 元优惠券。",
      intent: null,
      sources: null,
      created_at: "2026-11-11T10:21:00Z",
    });

    renderWithProviders(
      <Routes>
        <Route path="/admin/handoffs/:id" element={<HandoffDetailPage />} />
      </Routes>,
      { initialEntries: ["/admin/handoffs/7"] },
    );

    expect(await screen.findByText("可以便宜一点吗？")).toBeInTheDocument();
    await userEvent.type(
      screen.getByLabelText("人工回复"),
      "可以为您申请 20 元优惠券。",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "发送回复" }),
    );

    expect(mockedSendHandoffReply).toHaveBeenCalledWith(
      7,
      "可以为您申请 20 元优惠券。",
    );
    expect(
      await screen.findByText("已发送，会话保持进行中，可在「人工回复」继续跟进"),
    ).toBeInTheDocument();
  });
});
