import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { fetchMe } from "../../api/auth";
import { getToken } from "../../api/client";
import {
  listHandoffs,
  listKnowledge,
  listLogs,
} from "../../api/admin";
import { AdminApp } from "./AdminApp";

function renderAdmin() {
  return renderWithProviders(
    <Routes>
      <Route path="/admin/*" element={<AdminApp />} />
    </Routes>,
    {
      initialEntries: ["/admin"],
    },
  );
}

vi.mock("../../api/auth", () => ({
  fetchMe: vi.fn(),
  loginRequest: vi.fn(),
  logoutRequest: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  getToken: vi.fn(),
  setToken: vi.fn(),
}));

vi.mock("../../api/admin", () => ({
  listHandoffs: vi.fn(),
  listKnowledge: vi.fn(),
  getKnowledge: vi.fn(),
  upsertKnowledge: vi.fn(),
  listLogs: vi.fn(),
}));

const mockedFetchMe = fetchMe as unknown as ReturnType<typeof vi.fn>;
const mockedGetToken = getToken as unknown as ReturnType<typeof vi.fn>;
const mockedListHandoffs = listHandoffs as unknown as ReturnType<typeof vi.fn>;
const mockedListKnowledge =
  listKnowledge as unknown as ReturnType<typeof vi.fn>;
const mockedListLogs = listLogs as unknown as ReturnType<typeof vi.fn>;

describe("AdminApp knowledge navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedGetToken.mockReturnValue("admin-token");
    mockedFetchMe.mockResolvedValue({
      id: 1,
      username: "admin",
      role: "admin",
    });
    mockedListHandoffs.mockResolvedValue([]);
    mockedListKnowledge.mockResolvedValue([]);
    mockedListLogs.mockResolvedValue({
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    });
  });

  it("lands on the handoff queue without a service overview", async () => {
    renderAdmin();

    expect(
      await screen.findByRole("heading", { name: "工单队列" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "服务概览" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "返回客服台" }),
    ).not.toBeInTheDocument();
  });

  it("opens the knowledge workspace from the admin navigation", async () => {
    renderAdmin();

    const knowledgeLink = await screen.findByRole("link", {
      name: "知识库",
    });
    await userEvent.click(knowledgeLink);

    expect(
      await screen.findByRole("heading", { name: "知识库" }),
    ).toBeInTheDocument();
  });

  it("opens operational logs from the admin navigation", async () => {
    renderAdmin();

    const logsLink = await screen.findByRole("link", {
      name: "运行日志",
    });
    await userEvent.click(logsLink);

    expect(
      await screen.findByRole("heading", { name: "运行日志" }),
    ).toBeInTheDocument();
  });
});
