import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { MessageBubble } from "../../components/MessageBubble";
import type { ChatMessage } from "../../api/chat";

function msg(overrides: Partial<ChatMessage>): ChatMessage {
  return {
    id: 1,
    role: "assistant",
    content: "您好",
    intent: null,
    sources: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("MessageBubble", () => {
  it("renders sources for assistant answers", () => {
    renderWithProviders(
      <MessageBubble
        message={msg({
          content: "根据资料回答",
          sources: [
            {
              title: "退换货政策",
              source_uri: "p/p.md",
              kb_type: "policy",
              parent_id: 1,
              chunk_id: 2,
              score: 0.9,
            },
          ],
        })}
      />,
    );
    expect(screen.getByTestId("message-sources")).toBeInTheDocument();
    expect(screen.getByText("退换货政策")).toBeInTheDocument();
  });

  it("renders handoff status for handoff intent", () => {
    renderWithProviders(
      <MessageBubble
        message={msg({ intent: "handoff", content: "已转接人工" })}
      />,
    );
    expect(screen.getByText("已转接人工客服")).toBeInTheDocument();
  });

  it("renders user message without sources", () => {
    renderWithProviders(
      <MessageBubble message={msg({ role: "user", content: "你好" })} />,
    );
    expect(screen.getByText("你好")).toBeInTheDocument();
    expect(screen.queryByTestId("message-sources")).not.toBeInTheDocument();
  });

  it("renders an administrator reply as human customer service", () => {
    renderWithProviders(
      <MessageBubble
        message={msg({
          role: "admin",
          content: "这是人工客服回复",
          intent: null,
        })}
      />,
    );

    expect(screen.getByText("人工客服")).toBeInTheDocument();
    expect(screen.getByText("这是人工客服回复")).toBeInTheDocument();
  });

  it("renders role avatars for customer, AI and human replies", () => {
    renderWithProviders(
      <>
        <MessageBubble message={msg({ role: "user", content: "你好" })} />
        <MessageBubble message={msg({ role: "assistant", content: "您好" })} />
        <MessageBubble message={msg({ role: "admin", content: "人工回复" })} />
      </>,
    );

    expect(screen.getByRole("img", { name: "顾客" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "AI 客服" })).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "人工客服" }),
    ).toBeInTheDocument();
  });

  it("renders structured after-sale records", () => {
    renderWithProviders(
      <MessageBubble
        message={msg({
          content: "查询到一条售后记录",
          after_sale: {
            status: "has_records",
            order_no: "ORD-6001",
            order_status: "已完成",
            service_type: "退款",
            handoff_required: false,
            records: [
              {
                id: 1,
                service_type: "退款",
                status: "已完成",
                refund_amount: "899.00",
                reason: "价保退款",
                created_at: "2026-11-11T10:20:00Z",
              },
            ],
          },
        })}
      />,
    );

    const panel = screen.getByRole("region", {
      name: "订单 ORD-6001 售后信息",
    });
    expect(panel).toHaveTextContent("订单状态：已完成");
    expect(panel).toHaveTextContent("退款");
    expect(panel).toHaveTextContent("已完成");
    expect(panel).toHaveTextContent("¥899.00");
    expect(panel).toHaveTextContent("价保退款");
    expect(panel).toHaveTextContent(/2026/);
  });

  it("renders a return request action and invokes the callback", async () => {
    const onReturnRequest = vi.fn();
    renderWithProviders(
      <MessageBubble
        message={msg({
          content: "可以申请退换货",
          actions: [
            {
              type: "return_request",
              label: "申请退换货",
              description: "上传商品照片",
            },
          ],
        })}
        onReturnRequest={onReturnRequest}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "申请退换货" }),
    );
    expect(onReturnRequest).toHaveBeenCalledOnce();
  });

  it("renders an explicit empty after-sale state", () => {
    renderWithProviders(
      <MessageBubble
        message={msg({
          after_sale: {
            status: "no_records",
            order_no: "ORD-7001",
            order_status: "已完成",
            service_type: "售后",
            records: [],
            handoff_required: false,
          },
        })}
      />,
    );

    expect(screen.getByRole("region", { name: "订单 ORD-7001 售后信息" }))
      .toHaveTextContent("暂无售后记录");
  });

  it("renders a non-enumerable unavailable after-sale state", () => {
    renderWithProviders(
      <MessageBubble
        message={msg({
          after_sale: {
            status: "not_available",
            order_no: "ORD-7101",
            order_status: null,
            service_type: "退款",
            records: [],
            handoff_required: false,
          },
        })}
      />,
    );

    const panel = screen.getByRole("region", {
      name: "订单 ORD-7101 售后信息",
    });
    expect(panel).toHaveTextContent("未查询到可展示的订单或售后信息");
    expect(panel).not.toHaveTextContent("已完成");
  });

  it("renders a sanitized query-error state without exposing handoff", () => {
    renderWithProviders(
      <MessageBubble
        message={msg({
          intent: "handoff",
          after_sale: {
            status: "query_error",
            order_no: "ORD-7201",
            order_status: "已完成",
            service_type: "售后",
            records: [],
            handoff_required: false,
          },
        })}
      />,
    );

    const panel = screen.getByRole("region", {
      name: "订单 ORD-7201 售后信息",
    });
    expect(panel).toHaveTextContent("售后查询暂时不可用");
    expect(panel).toHaveTextContent("请稍后重试，或换一种说法再问一次");
    expect(panel).not.toHaveTextContent("已转接人工客服核实");
    expect(panel).not.toHaveTextContent("SELECT");
    expect(panel).not.toHaveTextContent("postgresql://");
  });
});
