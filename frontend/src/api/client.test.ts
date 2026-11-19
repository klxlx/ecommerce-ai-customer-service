import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("apiFetch", () => {
  it("lets the browser set the multipart boundary for FormData uploads", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({ accepted: true }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const body = new FormData();
    body.set("file", new File(["FAQ"], "faq.txt", { type: "text/plain" }));

    await apiFetch("/api/admin/knowledge/preview", {
      method: "POST",
      body,
    });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.has("Content-Type")).toBe(false);
  });

  it("continues to send JSON content type for regular requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({ accepted: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ title: "测试" }),
    });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
  });
});
