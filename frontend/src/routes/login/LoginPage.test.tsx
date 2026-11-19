import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { LoginPage } from "./LoginPage";

vi.mock("../../api/auth", () => ({
  loginRequest: vi.fn(),
  fetchMe: vi.fn(),
  logoutRequest: vi.fn(),
}));

describe("LoginPage", () => {
  it("validates empty fields", async () => {
    renderWithProviders(<LoginPage />, { initialEntries: ["/login"] });
    await userEvent.click(screen.getByRole("button", { name: /登录/ }));
    expect(await screen.findByText("请输入用户名和密码")).toBeInTheDocument();
  });

  it("submits credentials", async () => {
    const { loginRequest } = await import("../../api/auth");
    const mocked = loginRequest as unknown as ReturnType<typeof vi.fn>;
    mocked.mockResolvedValue({
      token: "t",
      user: { id: 1, username: "customer", role: "customer" },
    });

    renderWithProviders(<LoginPage />, { initialEntries: ["/login"] });
    await userEvent.type(screen.getByLabelText("用户名"), "customer");
    await userEvent.type(screen.getByLabelText("密码"), "password123");
    await userEvent.click(screen.getByRole("button", { name: /登录/ }));

    expect(mocked).toHaveBeenCalledWith("customer", "password123");
  });
});
