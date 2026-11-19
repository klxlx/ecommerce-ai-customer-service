import { test, expect, type Page } from "@playwright/test";
import {
  createTestBatch,
  expectInsideViewport,
  expectNoPageOverflow,
  login,
  registerCurrentConversation,
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

test.describe("customer chat", () => {
  test("registers and removes conversations created by the test", async ({
    page,
  }) => {
    await login(page, "customer");
    const batch = testBatches.get(page)!;
    const conversationId = await startConversation(page, batch);

    await expect
      .poll(() => batch.conversationIds)
      .toContain(conversationId);

    const token = await page.evaluate(() => localStorage.getItem("cs_token"));
    expect(token).toBeTruthy();
    await batch.cleanup();

    const response = await page.request.get("/api/conversations", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.status()).toBe(200);
    const conversations = (await response.json()) as Array<{ id: number }>;
    expect(conversations.map((conversation) => conversation.id)).not.toContain(
      conversationId,
    );
  });

  test("login and greeting", async ({ page }) => {
    await login(page, "customer");
    await startConversation(page, testBatches.get(page)!);

    await page.getByLabel("输入消息").fill("你好");
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByTestId("chat-messages")).toContainText("购物客服");
  });

  test("handoff shows status", async ({ page }) => {
    await login(page, "customer");
    await startConversation(page, testBatches.get(page)!);

    await sendMessage(page, "转人工");

    await expect(page.getByTestId("chat-messages")).toContainText("人工");
  });

  test("after-sale records are shown as structured order facts", async ({
    page,
  }) => {
    await login(page, "customer");
    await startConversation(page, testBatches.get(page)!);

    await sendMessage(page, "查询订单 ORD-20261110-0015 的售后进度");

    const panel = page.getByRole("region", {
      name: "订单 ORD-20261110-0015 售后信息",
    });
    await expect(panel).toContainText("订单状态：已签收");
    await expect(panel).toContainText("退货退款");
    await expect(panel).toContainText("已完成");
    await expect(panel).toContainText("退款金额：¥88.50");
    await expect(panel).toContainText("原因：商品破损");
    await expect(panel).toContainText("申请时间：");
  });

  test("after-sale empty and unavailable states are distinct", async ({
    page,
  }) => {
    await login(page, "customer");
    await startConversation(page, testBatches.get(page)!);

    await sendMessage(page, "查询订单 ORD-20261117-0001 的售后进度");
    const emptyPanel = page.getByRole("region", {
      name: "订单 ORD-20261117-0001 售后信息",
    });
    await expect(emptyPanel).toContainText("暂无售后记录");
    await expect(emptyPanel).toContainText("订单状态：运输中");

    await sendMessage(page, "查询订单 ORD-99999999-9999 的售后进度");
    const unavailablePanel = page.getByRole("region", {
      name: "订单 ORD-99999999-9999 售后信息",
    });
    await expect(unavailablePanel).toContainText(
      "未查询到可展示的订单或售后信息",
    );
    await expect(unavailablePanel).not.toContainText("订单状态");
  });

  test("new refund above threshold hands off without writing after-sale data", async ({
    page,
  }) => {
    await login(page, "customer");
    await startConversation(page, testBatches.get(page)!);

    await sendMessage(
      page,
      "我要退款，订单 ORD-20261110-0015，金额 ¥500.37",
    );

    const messages = page.getByTestId("chat-messages");
    await expect(messages).toContainText("需要人工客服进一步核实");
    await expect(messages).toContainText("已转接人工客服");
    const panel = page.getByRole("region", {
      name: "订单 ORD-20261110-0015 售后信息",
    });
    await expect(panel).toContainText("暂无售后记录");
    await expect(panel).not.toContainText("退款金额：¥500.37");
  });

  test("refund clarification resumes when the order number is supplied", async ({
    page,
  }) => {
    await login(page, "customer");
    await startConversation(page, testBatches.get(page)!);

    await sendMessage(page, "我要退款");

    const assistantMessages = page
      .getByTestId("chat-messages")
      .locator(".message.assistant .message-bubble");
    await expect(assistantMessages.last()).toContainText("订单号是多少");

    await sendMessage(page, "ORD-20261110-0015");

    await expect(assistantMessages.last()).toContainText(
      "订单 ORD-20261110-0015 的退款",
    );
    await expect(assistantMessages.last()).toContainText("订单详情页");
    await expect(assistantMessages.last()).not.toContainText("订单号是多少");
  });

  test("price clarification resumes when the product name is supplied", async ({
    page,
  }) => {
    await login(page, "customer");
    await startConversation(page, testBatches.get(page)!);

    await sendMessage(page, "查询价格");

    const assistantMessages = page
      .getByTestId("chat-messages")
      .locator(".message.assistant .message-bubble");
    await expect(assistantMessages.last()).toContainText(
      "想查询哪件商品的价格",
    );

    await sendMessage(page, "恒温智能保温杯");

    await expect(assistantMessages.last()).toContainText(
      "商品「恒温智能保温杯」",
    );
    await expect(assistantMessages.last()).toContainText("¥88.50");
    await expect(assistantMessages.last()).toContainText("库存 120 件");
  });

  test("mobile chat is full-width, drawer-based, and unclipped", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "mobile-only layout check");

    const viewport = page.viewportSize()!;
    await login(page, "customer");

    // Deliberate conversation drawer replaces the permanent rail.
    const rail = page.locator(".conversation-rail");
    await expect(rail).toBeHidden();
    const toggle = page.getByRole("button", { name: "打开会话列表" });
    await expectInsideViewport(page, toggle, "drawer toggle");
    await toggle.click();
    await expect(rail).toBeVisible();
    await expect(page.locator(".rail-backdrop")).toBeVisible();
    // Wait for the slide-in transition before measuring and capturing.
    await expect
      .poll(async () => (await rail.boundingBox())?.x ?? -999, {
        timeout: 2_000,
      })
      .toBeGreaterThanOrEqual(0);
    await page.screenshot({
      path: testInfo.outputPath("mobile-customer-drawer.png"),
      fullPage: true,
    });

    const railBox = await rail.boundingBox();
    expect(railBox).not.toBeNull();
    expect(railBox!.width).toBeLessThan(viewport.width);

    await page.getByRole("button", { name: "新建会话" }).click();
    await registerCurrentConversation(page, testBatches.get(page)!);
    await expect(rail).toBeHidden();
    await expect(page.locator(".rail-backdrop")).toHaveCount(0);

    // Chat panel occupies the full viewport width.
    const panelBox = await page.locator(".chat-panel").boundingBox();
    expect(panelBox).not.toBeNull();
    expect(panelBox!.x).toBeLessThanOrEqual(1);
    expect(panelBox!.width).toBeGreaterThanOrEqual(viewport.width - 1);

    // Header title is not clipped.
    const title = page.locator(".chat-title");
    await expectInsideViewport(page, title, "chat title");
    const titleMetrics = await title.evaluate((el) => ({
      clientWidth: el.clientWidth,
      scrollWidth: el.scrollWidth,
    }));
    expect(titleMetrics.scrollWidth).toBeLessThanOrEqual(
      titleMetrics.clientWidth + 1,
    );

    // Suggestions, composer, and send control fit inside the viewport.
    const firstSuggestion = page.locator(".suggestion").first();
    const textarea = page.getByLabel("输入消息");
    const send = page.getByRole("button", { name: "发送" });
    await expectInsideViewport(page, firstSuggestion, "suggestion chip");
    await expectInsideViewport(page, textarea, "composer textarea");
    await expectInsideViewport(page, send, "send button");

    const metrics = await textarea.evaluate((el: HTMLTextAreaElement) => ({
      clientWidth: el.clientWidth,
      scrollWidth: el.scrollWidth,
      clientHeight: el.clientHeight,
      scrollHeight: el.scrollHeight,
    }));
    expect(metrics.clientWidth).toBeGreaterThan(viewport.width * 0.6);
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
    expect(metrics.scrollHeight).toBeLessThanOrEqual(metrics.clientHeight + 1);

    // Knowledge answer sources also fit inside the viewport.
    await page.getByRole("button", { name: "退换货政策是什么？" }).click();
    await expect(textarea).toHaveValue("退换货政策是什么？");
    await expect(send).toBeEnabled();
    const sendResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        /\/api\/conversations\/\d+\/messages$/.test(response.url()),
    );
    await send.click();
    const sendResponse = await sendResponsePromise;
    expect(sendResponse.status()).toBe(200);
    const sources = page.getByTestId("message-sources").last();
    await expect(sources).toBeVisible({ timeout: 15_000 });
    const chip = sources.locator(".source-chip").first();
    await chip.scrollIntoViewIfNeeded();
    const chipBox = await chip.boundingBox();
    expect(chipBox).not.toBeNull();
    expect(chipBox!.x).toBeGreaterThanOrEqual(-0.5);
    expect(chipBox!.x + chipBox!.width).toBeLessThanOrEqual(
      viewport.width + 0.5,
    );

    await expectNoPageOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath("mobile-customer-chat.png"),
      fullPage: true,
    });
  });
});
