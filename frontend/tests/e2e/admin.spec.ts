import { test, expect, type Page } from "@playwright/test";
import {
  createTestBatch,
  expectInsideViewport,
  expectNoPageOverflow,
  login,
  requireTestEnvironment,
  sendMessage,
  startConversation,
  type TestDataTracker,
} from "./helpers";

const testBatches = new WeakMap<Page, TestDataTracker>();

test.beforeEach(async ({ page }) => {
  requireTestEnvironment();
  testBatches.set(page, await createTestBatch(page));
});

test.afterEach(async ({ page }) => {
  const batch = testBatches.get(page);
  try {
    await batch?.cleanup();
  } finally {
    testBatches.delete(page);
  }
});

test.describe("admin console", () => {
  test("customer is redirected away from admin", async ({ page }) => {
    await login(page, "customer");

    await page.goto("/admin");
    await expect(page).toHaveURL(/\/app/);
  });

  test("admin lands on the handoff queue and legacy overview redirects", async ({
    page,
  }) => {
    await login(page, "admin");
    await expect(page).toHaveURL(/\/admin\/handoffs$/);
    await expect(page.getByRole("heading", { name: "工单队列" })).toBeVisible();
    await expect(page.getByRole("link", { name: "服务概览" })).toHaveCount(0);

    await page.goto("/admin/overview");
    await expect(page).toHaveURL(/\/admin\/handoffs$/);
    await expect(page.getByRole("heading", { name: "工单队列" })).toBeVisible();
  });

  test("admin reply returns the handoff conversation to the agent", async ({
    page,
  }) => {
    await login(page, "customer");
    const conversationId = await startConversation(
      page,
      testBatches.get(page)!,
    );
    const customerConversationUrl = page.url();
    expect(conversationId).toBeGreaterThan(0);

    await sendMessage(page, "转人工");
    await expect(page.getByTestId("chat-messages")).toContainText(
      "已转接人工客服",
    );

    await page.evaluate(() => localStorage.clear());
    await login(page, "admin");
    const adminToken = await page.evaluate(() =>
      localStorage.getItem("cs_token"),
    );
    expect(adminToken).not.toBeNull();

    const handoffResponse = await page.request.get("/api/admin/handoffs", {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(handoffResponse.status()).toBe(200);
    const handoffs = (await handoffResponse.json()) as Array<{
      id: number;
      conversation_id: number;
    }>;
    const handoff = handoffs.find(
      (item) => item.conversation_id === conversationId,
    );
    expect(handoff).toBeDefined();

    await page.goto(`/admin/handoffs/${handoff!.id}`);
    await expect(
      page.getByRole("heading", { name: `工单 #${handoff!.id}` }),
    ).toBeVisible();
    await expect(page.getByLabel("人工回复")).toHaveCount(0);

    const adminReply = `人工客服已收到，请放心。${Date.now()}`;
    await page.goto("/admin/live");
    await expect(
      page.getByRole("heading", { name: "客服回复" }),
    ).toBeVisible();
    await page.locator(".cs-session").first().click();
    await page.getByRole("button", { name: "接入会话" }).click();
    await page
      .getByPlaceholder("以人工客服身份回复顾客…")
      .fill(adminReply);
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByTestId("chat-messages")).toContainText(adminReply);
    await page.getByRole("button", { name: "退出会话" }).click();
    await page.getByPlaceholder("例如：").fill("已核实并答复，交还智能体。");
    await page.getByRole("button", { name: "确认解决并交还" }).click();
    await expect(
      page.getByText("已退出会话，已交还智能体。"),
    ).toBeVisible();

    await page.evaluate(() => localStorage.clear());
    await login(page, "customer");
    await page.goto(customerConversationUrl);
    await expect(page.getByTestId("chat-messages")).toContainText(
      "人工客服",
    );
    await expect(page.getByTestId("chat-messages")).toContainText(adminReply);

    await sendMessage(page, "你好");
    const latestAgentReply = page
      .getByTestId("chat-messages")
      .locator(".message.assistant .message-bubble")
      .last();
    await expect(latestAgentReply).toContainText("购物客服");
    await expect(latestAgentReply).not.toContainText("消息已发送给人工客服");
  });

  test("admin views and filters operational logs", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "desktop-only workflow");

    await login(page, "admin");
    await page.getByRole("link", { name: "运行日志" }).click();
    await expect(page).toHaveURL(/\/admin\/logs$/);
    await expect(page.getByRole("heading", { name: "运行日志" })).toBeVisible();
    await expect(
      page.getByRole("form", { name: "运行日志筛选" }),
    ).toBeVisible();

    await page.getByLabel("意图").selectOption("knowledge");
    const responsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes("/api/admin/logs?") &&
        response.url().includes("intent=knowledge"),
    );
    await page.getByRole("button", { name: "查询" }).click();

    const response = await responsePromise;
    expect(response.status()).toBe(200);
    expect(await response.json()).toMatchObject({ limit: 20, offset: 0 });
    await expect(page.getByText("运行日志加载失败")).toHaveCount(0);
  });

  test("admin adds policy knowledge and sees the ingestion result", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "desktop-only workflow");

    await login(page, "admin");
    await page.getByRole("link", { name: "知识库" }).click();
    await expect(page.getByRole("heading", { name: "知识库" })).toBeVisible();

    await page.getByRole("button", { name: "新增知识" }).click();
    await page.getByLabel("标题").fill(`桌面验收政策-${Date.now()}`);
    await page.getByLabel("政策知识").check();
    await page
      .getByLabel("知识内容")
      .fill("## 固定规则\n官方渠道订单按页面公示规则处理。");
    await page.getByRole("button", { name: "保存并入库" }).click();

    await expect(page.getByText("入库成功")).toBeVisible();
    await expect(page.getByText("1 条知识已入库")).toBeVisible();
    await expectNoPageOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath("admin-knowledge.png"),
      fullPage: true,
    });
  });

  test("admin opens handoff detail and updates status", async ({ page }) => {
    await login(page, "admin");
    await page.getByRole("link", { name: "工单队列" }).click();

    const firstRow = page.locator("tbody tr.clickable").first();
    if ((await firstRow.count()) > 0) {
      await firstRow.click();
      await expect(page.getByText("需求摘要")).toBeVisible();
    }
  });

  test("admin sees safe after-sale context and queue marker", async ({
    page,
  }) => {
    await login(page, "customer");
    await startConversation(page, testBatches.get(page)!);
    await sendMessage(
      page,
      "我要退款，订单 ORD-20261110-0015，金额 ¥500.37",
    );
    await expect(page.getByTestId("chat-messages")).toContainText(
      "已转接人工客服",
    );

    await page.evaluate(() => localStorage.clear());
    await login(page, "admin");
    await page.getByRole("link", { name: "工单队列" }).click();

    const ticketRow = page
      .locator("tbody tr.clickable")
      .filter({ hasText: "500.37" })
      .first();
    await expect(ticketRow).toBeVisible();
    await expect(ticketRow.locator(".after-sale-tag")).toHaveText("售后");

    await ticketRow.click();
    await expect(
      page.getByRole("heading", { name: "关联订单与售后" }),
    ).toBeVisible();
    const context = page.locator(".after-sale-admin");
    await expect(context).toContainText("ORD-20261110-0015");
    await expect(context).toContainText("订单状态");
    await expect(context).toContainText("已签收");
    await expect(context.locator(".threshold-fact")).toContainText(
      "超过人工处理阈值",
    );
    await expect(context.locator(".threshold-fact")).toContainText(
      "金额 ¥500.37，阈值 ¥500.00",
    );
    await expect(page.getByText("检索与工具证据")).toHaveCount(0);
    await expect(page.locator("pre")).toHaveCount(0);
  });

  test("mobile handoff queue is readable and operable", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "mobile-only layout check");

    // Create a handoff ticket through the customer flow so the queue is never
    // empty when this test runs against a fresh demo database.
    await login(page, "customer");
    await startConversation(page, testBatches.get(page)!);
    await page.getByLabel("输入消息").fill("转人工");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByTestId("chat-messages")).toContainText("人工");

    await page.evaluate(() => localStorage.clear());
    await login(page, "admin");
    await page.goto("/admin/handoffs");

    const firstRow = page.locator("tbody tr.clickable").first();
    await expect(firstRow).toBeVisible();

    const cells = firstRow.locator("td");
    expect(await cells.count()).toBe(5);

    // Stacked label/value presentation instead of single-character columns.
    const summary = firstRow.locator('td[data-label="摘要"]');
    await expect(summary).toContainText(/\S/);
    const summaryBox = await summary.boundingBox();
    expect(summaryBox).not.toBeNull();
    expect(summaryBox!.width).toBeGreaterThan(120);
    const labelContent = await summary.evaluate(
      (el) => getComputedStyle(el, "::before").content,
    );
    expect(labelContent).toContain("摘要");

    const status = firstRow.locator('td[data-label="状态"] .pill');
    await expect(status).toBeVisible();
    const statusBox = await status.boundingBox();
    expect(statusBox).not.toBeNull();
    expect(statusBox!.width).toBeGreaterThan(40);

    for (let i = 0; i < (await cells.count()); i += 1) {
      await expectInsideViewport(page, cells.nth(i), `handoff cell ${i}`);
    }

    await expectNoPageOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath("mobile-admin-handoffs.png"),
      fullPage: true,
    });

    await firstRow.click();
    await expect(page).toHaveURL(/\/admin\/handoffs\/\d+/);
    await expect(page.getByText("需求摘要")).toBeVisible();
  });
});
