// task2 无头 UI 走查：位置选择器（路口可搜索下拉 + 摄像头含「所有」）+ 事件数据库视图
// + 证据点击跳转定位。需先起 Kafka + ingest + 网关(新代码) + vite(18180)，并已有库内数据。
// 思路对齐 scripts/ui-qa-check.mjs（chromium，google-chrome 兜底，截图存 public/ui-screenshots）。
import { chromium } from "playwright";
import { existsSync, mkdirSync } from "node:fs";

const url = process.env.CHECK_URL || "http://127.0.0.1:18180/?source=gateway";
const SHOTS = "public/ui-screenshots";
const fallbackExecutables = [
  process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium-browser",
  "/usr/bin/chromium"
].filter(Boolean);

async function launchBrowser() {
  try {
    return await chromium.launch({ headless: true });
  } catch (error) {
    for (const executablePath of fallbackExecutables) {
      if (existsSync(executablePath)) return chromium.launch({ headless: true, executablePath });
    }
    throw error;
  }
}

const results = [];
async function runCheck(name, fn) {
  try {
    await fn();
    results.push([name, "PASS"]);
  } catch (error) {
    results.push([name, "FAIL", error instanceof Error ? error.message : String(error)]);
  }
}

if (!existsSync(SHOTS)) mkdirSync(SHOTS, { recursive: true });
const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

await page.goto(url, { waitUntil: "networkidle" });
await page.waitForSelector(".gateway-shell");
// 进入「监控视频流处理世界模型」
await page.getByRole("button", { name: /监控视频流处理世界模型/ }).click();
await page.locator(".video-qa--main").waitFor();

const qaPicker = page.locator(".video-qa-form .location-picker");

await runCheck("问答面板含位置选择器（路口 combobox + 摄像头 select）", async () => {
  if ((await qaPicker.count()) !== 1) throw new Error("QA form should have exactly one location-picker");
  if ((await qaPicker.locator(".lp-input").count()) !== 1) throw new Error("missing intersection input");
  if ((await qaPicker.locator(".lp-camera").count()) !== 1) throw new Error("missing camera select");
});

await runCheck("路口下拉可搜索、列出库内路口", async () => {
  await qaPicker.locator(".lp-input").click();
  await qaPicker.locator(".lp-menu").waitFor({ timeout: 4000 });
  const opts = await qaPicker.locator(".lp-option").count();
  if (opts < 2) throw new Error(`expected multiple intersection options, got ${opts}`);
  // 搜索过滤
  await qaPicker.locator(".lp-input").fill("gg-xiongchu-minzu");
  const filtered = qaPicker.locator(".lp-option", { hasText: "gg-xiongchu-minzu" });
  if ((await filtered.count()) < 1) throw new Error("search did not surface gg-xiongchu-minzu");
});

await runCheck("选定路口 → 摄像头下拉含「所有」+ 该路口各摄像头", async () => {
  await qaPicker.locator(".lp-option", { hasText: "gg-xiongchu-minzu" }).first().click();
  const sel = qaPicker.locator(".lp-camera");
  if (await sel.isDisabled()) throw new Error("camera select should be enabled after picking intersection");
  const optionTexts = await sel.locator("option").allInnerTexts();
  const joined = optionTexts.join(" | ");
  if (!optionTexts[0].includes("所有摄像头")) throw new Error(`first camera option should be 所有摄像头, got: ${optionTexts[0]}`);
  for (const cam of ["cam-minzu-east-001", "cam-minzu-west-002"]) {
    if (!joined.includes(cam)) throw new Error(`camera ${cam} missing from select: ${joined}`);
  }
});

await page.screenshot({ path: `${SHOTS}/task2-01-location-picker.png` });

await runCheck("提问（路口=所有摄像头）→ 返回可点击证据", async () => {
  await page.locator(".video-qa-question").fill("民族大道有没有事故？");
  await page.locator(".video-qa-submit").click();
  await page.locator(".video-qa-evi--clickable").first().waitFor({ timeout: 50000 });
  const n = await page.locator(".video-qa-evi--clickable").count();
  if (n < 1) throw new Error("no clickable evidence returned");
});

await page.screenshot({ path: `${SHOTS}/task2-02-clickable-evidence.png` });

await runCheck("点证据 → 跳「事件数据库」视图并打开该记录详情", async () => {
  await page.locator(".video-qa-evi--clickable").first().click();
  await page.locator(".event-db").waitFor({ timeout: 6000 });
  await page.locator(".event-db-drawer").waitFor({ timeout: 6000 });
  const bodyText = (await page.locator(".event-db-drawer-body").innerText()).trim();
  if (!bodyText) throw new Error("detail drawer body empty");
  // 数据库 tab 应处于 active
  const active = await page.locator(".video-wm-tab.active").innerText();
  if (!active.includes("事件数据库")) throw new Error(`active tab should be 事件数据库, got ${active}`);
});

await page.screenshot({ path: `${SHOTS}/task2-03-evidence-jump-detail.png` });

await runCheck("数据库视图：表格分页 + 筛选 + 行详情", async () => {
  // 关掉抽屉
  await page.locator(".event-db-close").click();
  // 表格有行
  const rows = await page.locator(".event-db-row").count();
  if (rows < 1) throw new Error("event-db table has no rows");
  // 按路口筛选（数据库视图自己的 picker）
  const dbPicker = page.locator(".event-db-toolbar .location-picker");
  await dbPicker.locator(".lp-input").click();
  await dbPicker.locator(".lp-menu").waitFor({ timeout: 4000 });
  await dbPicker.locator(".lp-option", { hasText: "gg-xiongchu-minzu" }).first().click();
  // 等待刷新后所有行属于该路口（路口列显示 intersection_id）
  await page.waitForTimeout(800);
  const filteredRows = await page.locator(".event-db-row").count();
  if (filteredRows < 1) throw new Error("no rows after intersection filter");
  const countText = await page.locator(".event-db-count").innerText();
  if (!/共\s*6\s*条/.test(countText)) throw new Error(`expected 共 6 条 for gg-xiongchu-minzu, got: ${countText}`);
  // 点首行 → 抽屉
  await page.locator(".event-db-row").first().click();
  await page.locator(".event-db-drawer").waitFor({ timeout: 6000 });
});

await page.screenshot({ path: `${SHOTS}/task2-04-database-view.png` });

await runCheck("切回「事件问答」视图", async () => {
  await page.locator(".event-db-close").click();
  await page.locator(".video-wm-tab", { hasText: "事件问答" }).click();
  await page.locator(".video-qa--main").waitFor({ timeout: 4000 });
});

await runCheck("无页面级横向溢出（桌面）", async () => {
  const m = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth
  }));
  if (m.scrollWidth > m.width + 1) throw new Error(`horizontal overflow ${JSON.stringify(m)}`);
});

await browser.close();
console.table(results);
const failed = results.filter((r) => r[1] === "FAIL");
if (failed.length) {
  for (const f of failed) console.error("FAIL:", f[0], "→", f[2]);
  process.exit(1);
}
console.log(`task2 UI check: ${results.length}/${results.length} PASS`);
