import { expect, type Locator, type Page } from "@playwright/test";

const TEST_MARKER = /(?:^|[/_.-])(test|e2e|eval)(?:[/_.-]|$)/i;

export type TestResourceKind = "conversation" | "knowledge";

export interface TestDataTracker {
  readonly batchId: string;
  readonly conversationIds: number[];
  readonly knowledgeIds: number[];
  registerConversation(id: number, authorization: string): void;
  cleanup(): Promise<void>;
}

function parseResourceId(
  kind: TestResourceKind,
  payload: unknown,
): number | null {
  if (!payload || typeof payload !== "object") return null;
  if (kind === "conversation") {
    const id = (payload as { id?: unknown }).id;
    return typeof id === "number" ? id : null;
  }
  const item = (payload as { item?: { id?: unknown } | null }).item;
  return typeof item?.id === "number" ? item.id : null;
}

function resourcePath(kind: TestResourceKind, id: number): string {
  return kind === "conversation"
    ? `/api/conversations/${id}`
    : `/api/admin/knowledge/${id}`;
}

export async function createTestBatch(page: Page): Promise<TestDataTracker> {
  const batchId = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  const ids: Record<TestResourceKind, number[]> = {
    conversation: [],
    knowledge: [],
  };
  const authorization = new Map<string, string>();
  const pendingRegistrations: Promise<void>[] = [];

  const track = (kind: TestResourceKind, id: number, token: string) => {
    if (!ids[kind].includes(id)) ids[kind].push(id);
    authorization.set(`${kind}:${id}`, token);
  };

  page.on("response", (response) => {
    const request = response.request();
    if (request.method() !== "POST" || !response.ok()) return;
    const path = new URL(response.url()).pathname;
    const kind: TestResourceKind | null =
      path === "/api/conversations"
        ? "conversation"
        : path === "/api/admin/knowledge"
          ? "knowledge"
          : null;
    if (!kind) return;

    const authHeader = request.headers().authorization;
    if (!authHeader) return;
    const registration = response
      .json()
      .then((payload) => {
        const id = parseResourceId(kind, payload);
        if (id != null) track(kind, id, authHeader);
      })
      .catch(() => {
        // A malformed response is surfaced by the test assertion that starts
        // the workflow; it must not break the response listener.
      });
    pendingRegistrations.push(registration);
  });

  return {
    batchId,
    get conversationIds() {
      return [...ids.conversation];
    },
    get knowledgeIds() {
      return [...ids.knowledge];
    },
    registerConversation(id, token) {
      if (!Number.isInteger(id) || id <= 0) {
        throw new Error(`${batchId}: invalid conversation id ${id}`);
      }
      if (!token) {
        throw new Error(`${batchId}: conversation ${id} is missing authorization`);
      }
      track("conversation", id, token);
    },
    async cleanup() {
      const failures: string[] = [];
      await Promise.all(pendingRegistrations);
      for (const kind of ["conversation", "knowledge"] as const) {
        for (const id of [...ids[kind]]) {
          const token = authorization.get(`${kind}:${id}`);
          if (!token) {
            failures.push(`${kind} ${id}: missing test authorization`);
            continue;
          }
          const response = await page.request.delete(resourcePath(kind, id), {
            headers: { Authorization: token },
          });
          if (response.status() !== 204 && response.status() !== 404) {
            failures.push(
              `${kind} ${id}: cleanup returned HTTP ${response.status()}`,
            );
          }
        }
      }
      if (failures.length > 0) {
        throw new Error(
          `E2E batch ${batchId} cleanup failed: ${failures.join("; ")}`,
        );
      }
    },
  };
}

export function requireTestEnvironment(): void {
  if (process.env.E2E_TEST_MODE === "1") return;
  const baseUrl = process.env.E2E_BASE_URL || "";
  if (baseUrl && TEST_MARKER.test(baseUrl)) return;
  throw new Error(
    "Refusing to run browser tests without an explicit test environment. " +
      "Set E2E_TEST_MODE=1 or mark E2E_BASE_URL as test/e2e/eval.",
  );
}

export async function login(page: Page, username: "customer" | "admin") {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill("password123");
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page).toHaveURL(username === "admin" ? /\/admin/ : /\/app/);
}

/** Register the conversation currently open in the customer app. */
export async function registerCurrentConversation(
  page: Page,
  batch: TestDataTracker,
): Promise<number> {
  await expect(page).toHaveURL(/\/app\/\d+/);
  const conversationId = Number(
    new URL(page.url()).pathname.match(/^\/app\/(\d+)/)?.[1],
  );
  if (!Number.isInteger(conversationId) || conversationId <= 0) {
    throw new Error(`Unable to read conversation id from ${page.url()}`);
  }
  const token = await page.evaluate(() => localStorage.getItem("cs_token"));
  if (!token) {
    throw new Error(`Conversation ${conversationId} is missing cs_token`);
  }
  batch.registerConversation(
    conversationId,
    token.startsWith("Bearer ") ? token : `Bearer ${token}`,
  );
  return conversationId;
}

/** Open the conversation drawer on mobile, then create and register a chat. */
export async function startConversation(
  page: Page,
  batch: TestDataTracker,
): Promise<number> {
  const viewport = page.viewportSize();
  if (viewport && viewport.width <= 720) {
    await page.getByRole("button", { name: "打开会话列表" }).click();
  }
  await page.getByRole("button", { name: "新建会话" }).click();
  return registerCurrentConversation(page, batch);
}

export async function sendMessage(page: Page, message: string) {
  await page.getByLabel("输入消息").fill(message);
  await page.getByRole("button", { name: "发送" }).click();
}

export async function expectNoPageOverflow(page: Page) {
  const sizes = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(sizes.scrollWidth, "page scrollWidth").toBeLessThanOrEqual(
    sizes.clientWidth,
  );
}

export async function expectInsideViewport(
  page: Page,
  locator: Locator,
  label: string,
) {
  const viewport = page.viewportSize();
  expect(viewport, `${label}: viewport`).not.toBeNull();
  await expect(locator, `${label}: visible`).toBeVisible();
  const box = await locator.boundingBox();
  expect(box, `${label}: bounding box`).not.toBeNull();
  expect(box!.x, `${label}: left edge`).toBeGreaterThanOrEqual(-0.5);
  expect(box!.y, `${label}: top edge`).toBeGreaterThanOrEqual(-0.5);
  expect(box!.x + box!.width, `${label}: right edge`).toBeLessThanOrEqual(
    viewport!.width + 0.5,
  );
  expect(box!.y + box!.height, `${label}: bottom edge`).toBeLessThanOrEqual(
    viewport!.height + 0.5,
  );
}
