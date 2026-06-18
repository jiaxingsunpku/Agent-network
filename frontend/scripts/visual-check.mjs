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

await page.getByRole("button", { name: /模型管理/ }).click();
await page.getByRole("heading", { name: "模型管理" }).waitFor();
await page.getByRole("button", { name: /MaxPressure 基线/ }).click();
await page.getByRole("button", { name: /启动评测/ }).click();
await page.locator('.tool-feedback:has-text("启动评测")').waitFor();

await page.getByRole("button", { name: /模型训练/ }).click();
await page.getByRole("heading", { name: "模型训练" }).waitFor();
await page.getByRole("heading", { name: "训练配置" }).waitFor();
await page.locator('label:has-text("数据来源") select').selectOption("train-output-net-207");
await page.getByText("output-net-207tls").waitFor();
await page.getByRole("button", { name: /归档训练/ }).click();
await page.locator('.tool-feedback:has-text("训练归档")').waitFor();

await page.getByRole("button", { name: /持续学习/ }).click();
await page.getByRole("heading", { name: "持续学习" }).waitFor();
await page.getByRole("button", { name: /夜间策略离线更新/ }).click();
await page.getByRole("button", { name: /手动触发/ }).click();
await page.locator('.tool-feedback:has-text("手动触发自动化")').waitFor();

await page.getByRole("button", { name: /监控视频流处理世界模型/ }).click();
await page.getByRole("button", { name: /视频流接入/ }).waitFor();
await page.getByText("实时摄像头").waitFor();
await page.getByRole("button", { name: /查看检测窗口/ }).click();
await page.locator('.tool-feedback:has-text("检测窗口已切换")').waitFor();

await page.getByRole("button", { name: /路口流量监控世界模型/ }).click();
await page.getByRole("button", { name: /实时交通数据/ }).click();
await page.getByRole("heading", { name: "实时交通数据" }).waitFor();
await page.locator('.tool-shell-panel .shell-stat-card:has-text("均速")').waitFor();
await page.getByRole("button", { name: /刷新窗口/ }).click();
await page.locator('.tool-feedback:has-text("刷新窗口")').waitFor();

await page.screenshot({ path: "visual-simplified-shell.png", fullPage: true });
await page.close();
await browser.close();
console.log("visual checks passed");
