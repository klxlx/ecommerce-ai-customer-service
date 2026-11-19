import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { listHandoffs, type HandoffTicket } from "../../api/admin";
import { HandoffsPage } from "./HandoffsPage";

vi.mock("../../api/admin", () => ({
  listHandoffs: vi.fn(),
}));

const mockedListHandoffs = listHandoffs as unknown as ReturnType<typeof vi.fn>;

function ticket(overrides: Partial<HandoffTicket>): HandoffTicket {
  return {
    id: 1,
    conversation_id: 1,
    user_id: 2,
    status: "open",
    summary: "测试工单",
    slots: {},
    recent_messages: [],
    evidence: { sources: [], tool: {} },
    created_at: "2026-11-11T10:20:00Z",
    updated_at: "2026-11-11T10:20:00Z",
    ...overrides,
  };
}

describe("HandoffsPage after-sale marker", () => {
  it("marks tickets carrying after-sale facts without marking unrelated tickets", async () => {
    mockedListHandoffs.mockResolvedValue([
      ticket({
        id: 8,
        summary: "退款金额超过上限",
        evidence: {
          sources: [],
          tool: {
            order: { order_no: "ORD-6002", status: "已完成" },
            threshold: {
              applied: true,
              amount: "500.01",
              limit: "500.00",
              exceeded: true,
            },
          },
        },
      }),
      ticket({ id: 9, summary: "用户要求人工" }),
    ]);

    renderWithProviders(<HandoffsPage />, {
      initialEntries: ["/admin/handoffs"],
    });

    expect(await screen.findByText("售后")).toBeInTheDocument();
    expect(screen.getByText("退款金额超过上限")).toBeInTheDocument();
    expect(screen.getByText("用户要求人工")).toBeInTheDocument();
    expect(screen.getAllByText("售后")).toHaveLength(1);
  });
});
