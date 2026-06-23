import { chromium } from "playwright";
import { existsSync } from "node:fs";

const url = process.env.CHECK_URL || "http://127.0.0.1:18184";
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

async function assertCanvasNonBlank(page, label) {
  const canvas = page.locator("canvas").first();
  await canvas.waitFor({ timeout: 10000 });
  const nonBlank = await canvas.evaluate((target) => {
    const ctx = target.getContext("2d", { willReadFrequently: true });
    if (!ctx) return true;
    const w = Math.min(180, target.width);
    const h = Math.min(140, target.height);
    const data = ctx.getImageData(0, 0, w, h).data;
    let colored = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i] < 245 || data[i + 1] < 245 || data[i + 2] < 245) colored += 1;
    }
    return colored > 120;
  });
  if (!nonBlank) throw new Error(`${label}: canvas appears blank`);
}

const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(url, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

await page.locator('.section-title:has-text("\u4e16\u754c\u6a21\u578b")').waitFor();
await page.getByText("系统运行中").waitFor();
await page.getByRole("button", { name: /智能信号灯世界模型/ }).waitFor();
await page.getByRole("button", { name: /监控视频流处理世界模型/ }).waitFor();
await page.getByRole("button", { name: /路口流量监控世界模型/ }).waitFor();
await page.getByRole("button", { name: /交通地图/ }).waitFor();
await assertCanvasNonBlank(page, "default traffic map");

await page.getByRole("button", { name: /控制策略/ }).click();
await page.getByRole("heading", { name: "控制策略" }).waitFor();
// 真·control_signal_inference：点「开始推理」→ 网关 /commands（真实下发，出现反馈）。
await page.getByRole("button", { name: /开始推理/ }).click();
await page.locator('.tool-feedback:has-text("下发")').waitFor();

// SignalTrain（训练/持续学习）模块已从 SV 信号世界模型移除，不再走查。
// 控制策略/路口指标/实时交通数据/智能体列表已接真实网关数据（snapshot + /commands）。

await page.getByRole("button", { name: /监控视频流处理世界模型/ }).click();
await page.getByRole("button", { name: /视频流接入/ }).waitFor();
await page.getByText("实时摄像头").waitFor();
await page.getByRole("button", { name: /查看检测窗口/ }).click();
await page.locator('.tool-feedback:has-text("检测窗口已切换")').waitFor();

await page.getByRole("button", { name: /路口流量监控世界模型/ }).click();
await page.getByRole("button", { name: /实时交通数据/ }).click();
await page.getByRole("heading", { name: "实时交通数据" }).waitFor();
await page.locator('.tool-shell-panel .shell-stat-card:has-text("均速")').waitFor();
// 实时交通数据已改真实 World Status 聚合（自动刷新，无 mock 刷新按钮）。

await page.screenshot({ path: "visual-simplified-shell.png", fullPage: true });
await page.close();
await browser.close();
console.log("visual checks passed");
