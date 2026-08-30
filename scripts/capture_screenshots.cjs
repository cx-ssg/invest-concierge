// Reasonix 正式截图脚本 —— 完整 Chromium（非 headless-shell）
// 用 NODE_PATH 指向全局 playwright 运行：NODE_PATH="C:/Users/cx101/AppData/Roaming/npm/node_modules" node capture.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const OUT = __dirname;
const BASE = 'http://localhost:8766';
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function shot(page, name, waitMs = 3000) {
  await sleep(waitMs);
  const p = path.join(OUT, name);
  await page.screenshot({ path: p, fullPage: false });
  const size = fs.statSync(p).size;
  console.log(`📸 ${name}  ${(size/1024).toFixed(1)}KB`);
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--window-size=1440,900'],
    channel: undefined,
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  // 1. 基金轨首页（默认 dashboard）
  try {
    await page.goto(BASE, { timeout: 60000 });
    await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(()=>{});
    await shot(page, 'screen-dashboard.png', 4000);
  } catch (e) { console.log('dashboard 失败:', String(e).slice(0,150)); }

  // 2. 诊断页（需要点击侧边栏导航）
  try {
    // 试试直接 URL（streamlit 有 ?page 或需点击）——先点击侧边栏"综合诊断"
    const diagBtn = page.locator('button:has-text("综合诊断")').first();
    if (await diagBtn.count()) {
      await diagBtn.click();
      await shot(page, 'screen-diagnosis.png', 5000);
    } else {
      console.log('未找到综合诊断按钮，尝试直接路径');
      await page.goto(BASE + '/?page=stock_diagnosis', { timeout: 30000 }).catch(()=>{});
      await shot(page, 'screen-diagnosis.png', 4000);
    }
  } catch (e) { console.log('diagnosis 失败:', String(e).slice(0,150)); }

  // 3. 设置页
  try {
    const setBtn = page.locator('button:has-text("系统设置")').first();
    if (await setBtn.count()) { await setBtn.click(); }
    else { await page.goto(BASE + '/?page=settings', { timeout: 30000 }).catch(()=>{}); }
    await shot(page, 'screen-settings.png', 3000);
  } catch (e) { console.log('settings 失败:', String(e).slice(0,150)); }

  // 4. 返回基金轨（资产总览）——双轨导航
  try {
    const homeBtn = page.locator('button:has-text("资产总览")').first();
    if (await homeBtn.count()) { await homeBtn.click(); }
    else { await page.goto(BASE, { timeout: 30000 }).catch(()=>{}); }
    await shot(page, 'screen-navigation.png', 3000);
  } catch (e) { console.log('navigation 失败:', String(e).slice(0,150)); }

  await browser.close();
  console.log('DONE');
})();