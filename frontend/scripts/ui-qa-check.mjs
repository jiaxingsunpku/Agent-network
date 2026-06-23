import { chromium } from "playwright";
import { existsSync } from "node:fs";

const url = process.env.CHECK_URL || "http://127.0.0.1:18180/?source=gateway";
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

async function assertNoPageOverflow(page, label) {
  const metrics = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    height: document.documentElement.clientHeight,
    scrollHeight: document.documentElement.scrollHeight
  }));
  if (metrics.scrollWidth > metrics.width || metrics.scrollHeight > metrics.height) {
    throw new Error(`${label}: page overflow ${JSON.stringify(metrics)}`);
  }
}

async function runCheck(name, fn, results) {
  try {
    await fn();
    results.push([name, "PASS"]);
  } catch (error) {
    results.push([name, "FAIL", error instanceof Error ? error.message : String(error)]);
  }
}

async function selectTool(page, name) {
  await page.locator(".gateway-tool-tab", { hasText: name }).click();
}

async function runDesktopInteractions(page, results) {
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForSelector(".gateway-shell");

  await runCheck("智能信号灯不显示视频问答", async () => {
    const count = await page.locator(".video-qa").count();
    if (count !== 0) throw new Error(`video QA visible: ${count}`);
  }, results);

  await runCheck("智能信号灯功能模块齐全（SV 推理/控制，无 SignalTrain 训练模块）", async () => {
    for (const title of ["交通地图", "实时交通数据", "控制策略", "智能体列表"]) {
      const count = await page.locator(".gateway-tool-tab", { hasText: title }).count();
      if (count !== 1) throw new Error(`missing or duplicated tool: ${title} (${count})`);
    }
    // SignalTrain（训练/评测/持续学习）模块不应出现在 SV 信号世界模型里。
    for (const title of ["模型训练", "对比实验", "持续学习"]) {
      const count = await page.locator(".gateway-tool-tab", { hasText: title }).count();
      if (count !== 0) throw new Error(`SignalTrain tool should be removed: ${title} (${count})`);
    }
  }, results);

  await runCheck("控制策略·真控制推理下发", async () => {
    // 真·control_signal_inference：点「开始推理」→ 网关 /commands（真实下发，出现反馈：已下发或下发失败）。
    await selectTool(page, "控制策略");
    await page.getByRole("button", { name: /开始推理/ }).click();
    await page.locator('.tool-feedback:has-text("下发")').waitFor();
  }, results);

  await runCheck("智能体列表·真 set_signal_plan 下发", async () => {
    // 默认选中可下发命令的执行体，点「下发相位方案」→ 真实 set_signal_plan 命令闭环（出现「已下发」反馈）。
    await selectTool(page, "智能体列表");
    await page.getByRole("button", { name: /下发相位方案/ }).click();
    await page.locator('.tool-feedback:has-text("下发")').waitFor();
  }, results);

  await runCheck("交通地图·切换路网下拉（真 set_signal_map 切图 + 几何变更）", async () => {
    // 交通地图右上「切换路网」下拉列 SV 可用地图；选一张 → 真实 set_signal_map 命令闭环 → SV 真切图。
    await selectTool(page, "交通地图");
    const select = page.locator(".large-map-mapswitch select");
    await select.waitFor({ timeout: 10000 });
    const optionCount = await select.locator("option").count();
    if (optionCount < 2) throw new Error(`map switch has no real map options (${optionCount})`);
    // 记录切图前 SV 路口数；index 0 是占位「切换路网…」，选 index 1 的真实地图下发切图命令。
    const preJc = await page.evaluate(async () => {
      const r = await fetch("/api/agent-network/sv-network", { cache: "no-store" });
      return (await r.json()).junction_count;
    });
    await select.selectOption({ index: 1 });
    await page.locator('.large-map-mapswitch small:has-text("下发")').waitFor({ timeout: 12000 });
    // 断言 SV 几何确实变了（set_signal_map 真切图，不只是发了命令）。
    await page.waitForFunction(async (pre) => {
      const r = await fetch("/api/agent-network/sv-network", { cache: "no-store" });
      const d = await r.json();
      return typeof d.junction_count === "number" && d.junction_count !== pre;
    }, preJc, { timeout: 20000 });
  }, results);

  await runCheck("视频模型：问答主界面 + 任务侧栏 + 去 mock 化", async () => {
    await page.getByRole("button", { name: /监控视频流处理世界模型/ }).click();
    await page.locator(".video-qa--main").waitFor();
    await page.locator(".task-sidebar").waitFor();
    // 交通 Inspector 隐藏。
    if ((await page.locator(".inspector").count()) !== 0) throw new Error("traffic inspector should be hidden in video model");
    // 去 mock：视频模型不再有老的功能模块工具条（VideoOpsPanel mock 已移除）。
    if ((await page.locator(".gateway-tool-tab").count()) !== 0) throw new Error("video model should not show mock tool tabs");
    // 命令模块诚实标注：检测/视频流/模型管理标为外部系统(vision hub) 占位。
    const chips = await page.locator(".task-placeholder-chip").allInnerTexts();
    const joined = chips.join("｜");
    for (const label of ["目标检测", "视频流接入", "模型管理"]) {
      if (!joined.includes(label)) throw new Error(`missing placeholder label: ${label} (got ${joined})`);
    }
  }, results);

  await runCheck("视频模型：新建协作任务→扇出定向命令→状态推进→回灌问答", async () => {
    // 新建任务（prompt 含「事故」，替身桩据此返回事故文本）。
    const stamp = String(Date.now()).slice(-6);
    const prompt = `UI自检 ${stamp}：民族大道最近有没有事故？`;
    await page.locator(".task-field textarea").fill(prompt);
    await page.getByRole("button", { name: /扇出协作命令/ }).click();
    // 新任务卡片出现，且显示参与 hub（target_agent_id chip）。
    const card = page.locator(".task-card", { hasText: prompt });
    await card.first().waitFor({ timeout: 12000 });
    if ((await card.first().locator(".task-hub").count()) < 1) throw new Error("task card should show ≥1 hub chip (directed fan-out)");
    // 等状态推进到「已聚合」（桥+桩+ingest 回流后），最多 ~24s。
    await card.first().locator(".task-status-pill", { hasText: "已聚合" }).waitFor({ timeout: 24000 });
    // 点任务把聚合结果回灌问答主界面。
    await card.first().click();
    await page.locator(".video-qa-task-banner").waitFor({ timeout: 8000 });
    const answer = (await page.locator(".video-qa--main .video-qa-answer").first().innerText()).trim();
    if (!answer) throw new Error("aggregated answer not fed back into main QA");
  }, results);

  await runCheck("路口流量·实时数据+路口指标(真 World Status)", async () => {
    await page.getByRole("button", { name: /路口流量监控世界模型/ }).click();
    await selectTool(page, "实时交通数据");
    await page.getByRole("heading", { name: "实时交通数据" }).waitFor();  // 真实聚合，自动刷新（无 mock 按钮）
    await selectTool(page, "路口指标");
    await page.getByRole("button", { name: /生成路口画像/ }).click();
    await page.locator('.tool-feedback:has-text("生成路口画像")').waitFor();
  }, results);

  await runCheck("桌面无页面级溢出", () => assertNoPageOverflow(page, "desktop"), results);
}

async function runResponsiveChecks(browser, results) {
  for (const viewport of [
    { name: "narrow", width: 900, height: 820 },
    { name: "mobile", width: 390, height: 820 }
  ]) {
    const page = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } });
    page.setDefaultTimeout(8000);
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForSelector(".gateway-shell");
    await runCheck(`${viewport.name} 无页面级溢出`, () => assertNoPageOverflow(page, viewport.name), results);
    await runCheck(`${viewport.name} Inspector 默认收起`, async () => {
      const cls = (await page.locator(".inspector").first().getAttribute("class")) || "";
      if (!cls.includes("collapsed")) throw new Error(`inspector class=${cls}`);
    }, results);
    if (viewport.width <= 760) {
      await page.locator(".mobile-world-switcher button", { hasText: "监控视频流处理世界模型" }).click();
    } else {
      await page.getByRole("button", { name: /监控视频流处理世界模型/ }).click();
    }
    await page.locator(".video-qa--main").waitFor();
    await runCheck(`${viewport.name} 视频模型问答主界面 + 任务侧栏`, async () => {
      if ((await page.locator(".task-sidebar").count()) === 0) throw new Error("task sidebar missing");
    }, results);
    await runCheck(`${viewport.name} 视频模型隐藏交通 Inspector`, async () => {
      const count = await page.locator(".inspector").count();
      if (count !== 0) throw new Error(`inspector visible: ${count}`);
    }, results);
    await page.close();
  }
}

const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.setDefaultTimeout(8000);
const results = [];

await runDesktopInteractions(page, results);
await page.close();
await runResponsiveChecks(browser, results);
await browser.close();

console.table(results);
const failed = results.filter((row) => row[1] !== "PASS");
if (failed.length) process.exit(1);
