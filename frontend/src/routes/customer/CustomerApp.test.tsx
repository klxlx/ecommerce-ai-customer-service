import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import {
  createConversation,
  deleteConversation,
  listConversations,
  listMessages,
  sendMessage,
} from "../../api/chat";
import { CustomerApp } from "./CustomerApp";

const logoutMock = vi.fn();
const userState = {
  username: "顾客小周",
  role: "customer" as "customer" | "admin",
};

vi.mock("../../auth/AuthContext", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../../auth/AuthContext")>();
  return {
    ...actual,
    useAuth: () => ({
      user: userState,
      loading: false,
      login: vi.fn(),
      logout: logoutMock,
    }),
  };
});

vi.mock("../../api/chat", () => ({
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  listConversations: vi.fn(),
  listMessages: vi.fn(),
  sendMessage: vi.fn(),
}));

const mockedCreateConversation =
  createConversation as unknown as ReturnType<typeof vi.fn>;
const mockedDeleteConversation =
  deleteConversation as unknown as ReturnType<typeof vi.fn>;
const mockedListConversations =
  listConversations as unknown as ReturnType<typeof vi.fn>;
const mockedListMessages =
  listMessages as unknown as ReturnType<typeof vi.fn>;
const mockedSendMessage = sendMessage as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  userState.username = "顾客小周";
  userState.role = "customer";
  mockedListConversations.mockResolvedValue([]);
  mockedListMessages.mockResolvedValue([]);
  mockedCreateConversation.mockResolvedValue({
    id: 1,
    title: "新会话",
    created_at: "2026-11-18T08:00:00Z",
    updated_at: "2026-11-18T08:00:00Z",
  });
  mockedDeleteConversation.mockResolvedValue(undefined);
  mockedSendMessage.mockResolvedValue({});
});

describe("CustomerApp", () => {
  it("uses the conversation title without exposing the internal id", async () => {
    mockedListConversations.mockResolvedValue([
      {
        id: 114,
        title: "耳机咨询",
        created_at: "2026-11-18T08:00:00Z",
        updated_at: "2026-11-18T08:05:00Z",
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/app/*" element={<CustomerApp />} />
      </Routes>,
      {
      initialEntries: ["/app/114"],
      },
    );

    expect(
      await screen.findByText("耳机咨询", { selector: ".chat-title" }),
    ).toBeInTheDocument();
    const rail = screen.getByRole("complementary", { name: "会话列表" });
    expect(await within(rail).findByText("耳机咨询")).toBeInTheDocument();
    expect(within(rail).queryByText(/会话\s*#/)).not.toBeInTheDocument();
    expect(
      screen.getByText("耳机咨询", { selector: ".chat-title" }),
    ).not.toHaveTextContent(/会话\s*#/);
  });

  it("falls back to the assistant name when the conversation has no title", async () => {
    mockedListConversations.mockResolvedValue([
      {
        id: 115,
        title: "",
        created_at: "2026-11-18T08:00:00Z",
        updated_at: "2026-11-18T08:05:00Z",
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/app/*" element={<CustomerApp />} />
      </Routes>,
      {
      initialEntries: ["/app/115"],
      },
    );

    expect(
      await screen.findByText("智能客服", { selector: ".chat-title" }),
    ).toBeInTheDocument();
    const rail = screen.getByRole("complementary", { name: "会话列表" });
    expect(await within(rail).findByText("智能客服")).toBeInTheDocument();
    expect(within(rail).queryByText(/会话\s*#/)).not.toBeInTheDocument();
    expect(
      screen.getByText("智能客服", { selector: ".chat-title" }),
    ).not.toHaveTextContent(/会话\s*#/);
  });

  it("automatically opens the most recent conversation from the customer home", async () => {
    mockedListConversations.mockResolvedValue([
      {
        id: 7,
        title: "最近订单咨询",
        created_at: "2026-11-18T08:00:00Z",
        updated_at: "2026-11-18T08:05:00Z",
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/app/*" element={<CustomerApp />} />
      </Routes>,
      {
        initialEntries: ["/app"],
      },
    );

    expect(
      await screen.findByText("最近订单咨询", { selector: ".chat-title" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(mockedListMessages).toHaveBeenCalledWith(7));
  });

  it("starts a conversation when returning to an empty customer desk", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/app/*" element={<CustomerApp />} />
      </Routes>,
      {
        initialEntries: ["/app?start=1"],
      },
    );

    await waitFor(() =>
      expect(mockedCreateConversation).toHaveBeenCalledTimes(1),
    );
    expect(
      await screen.findByText("新会话", { selector: ".chat-title" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(mockedListMessages).toHaveBeenCalledWith(1));
  });

  it("renders one conversation rail while preserving account and admin entry", async () => {
    userState.username = "管理员";
    userState.role = "admin";

    renderWithProviders(<CustomerApp />, {
      initialEntries: ["/app"],
    });

    expect(
      await screen.findByRole("complementary", { name: "会话列表" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("complementary")).toHaveLength(1);
    expect(screen.getByText("管理员")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "管理控制台" }),
    ).toHaveAttribute("href", "/admin");
    expect(
      screen.getByRole("button", { name: "退出登录" }),
    ).toBeInTheDocument();
  });

  it("requires confirmation before deleting a conversation", async () => {
    mockedListConversations.mockResolvedValue([
      {
        id: 7,
        title: "耳机咨询",
        created_at: "2026-11-18T08:00:00Z",
        updated_at: "2026-11-18T08:05:00Z",
      },
    ]);

    renderWithProviders(<CustomerApp />, {
      initialEntries: ["/app"],
    });

    await userEvent.click(
      await screen.findByRole("button", { name: "删除会话 耳机咨询" }),
    );
    expect(mockedDeleteConversation).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() =>
      expect(mockedDeleteConversation).toHaveBeenCalledWith(7),
    );
  });
});
