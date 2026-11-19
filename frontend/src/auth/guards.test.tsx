import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/render";
import { RequireAuth } from "./RequireAuth";
import { RequireAdmin } from "./RequireAdmin";
import { fetchMe } from "../api/auth";
import { getToken } from "../api/client";

vi.mock("../api/auth", () => ({
  loginRequest: vi.fn(),
  fetchMe: vi.fn(),
  logoutRequest: vi.fn(),
}));

vi.mock("../api/client", () => ({
  getToken: vi.fn(),
  setToken: vi.fn(),
}));

const mockedFetchMe = fetchMe as unknown as ReturnType<typeof vi.fn>;
const mockedGetToken = getToken as unknown as ReturnType<typeof vi.fn>;

describe("route guards", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("redirects unauthenticated users to login", async () => {
    mockedGetToken.mockReturnValue(null);

    renderWithProviders(
      <Routes>
        <Route element={<RequireAuth />}>
          <Route path="/app" element={<div>protected</div>} />
        </Route>
        <Route path="/login" element={<div>login page</div>} />
      </Routes>,
      { initialEntries: ["/app"] },
    );

    expect(await screen.findByText("login page")).toBeInTheDocument();
  });

  it("redirects customers away from admin routes", async () => {
    mockedGetToken.mockReturnValue("some-token");
    mockedFetchMe.mockResolvedValue({
      id: 1,
      username: "customer",
      role: "customer",
    });

    renderWithProviders(
      <Routes>
        <Route element={<RequireAdmin />}>
          <Route path="/admin" element={<div>admin area</div>} />
        </Route>
        <Route path="/app" element={<div>customer area</div>} />
      </Routes>,
      { initialEntries: ["/admin"] },
    );

    await waitFor(() =>
      expect(screen.getByText("customer area")).toBeInTheDocument(),
    );
  });
});
