#!/usr/bin/env node
/**
 * H5 **离线 DOM 回归 harness**（市场行情 P2 / 自选股 P3）。
 *
 * 为什么存在：H5 的两个新页面此前只有后端 API 证据（`report-H5.md` 的原始输出）与
 * `tsc -b` 类型检查——**页面渲染语义**（tab 切换、涨跌色、降级文案、持仓市值/盈亏）
 * 没有任何自动化闸门：把「数据不可得」分支删掉、或把涨跌色写反，pytest/tsc/build 全绿。
 * 本脚本把**真 `MarketPage` / `WatchlistPage`**（`frontend/harness/h5_dom_entry.tsx`，
 * rolldown 打成单文件 bundle）挂进 jsdom，用桩 HTTP 喂**与真后端同构的响应体**跑断言。
 *
 * 用法：
 *   node scripts/verify_h5_pages.mjs                       # 全绿才算过（退出码 0）
 *   node scripts/verify_h5_pages.mjs --mutate market-degraded-hidden
 *                                                          # 变异自检：删掉板块降级分支，
 *                                                          # 断言 harness **必须变红**
 * 产物：bundle 落系统临时目录（或 `AUDIT_OUT_DIR`）；结果 JSON 同目录，路径打印在末尾。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const FRONTEND = path.join(ROOT, 'frontend')
const requireFromFrontend = createRequire(path.join(FRONTEND, 'package.json'))

// ==================== 命令行 ====================
const argv = process.argv.slice(2)
function argValue(flag, dflt = null) {
  const i = argv.indexOf(flag)
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : dflt
}
const MUTATE = argValue('--mutate')
const KEEP = argv.includes('--keep')
const OUT_DIR = argValue('--out') || process.env.AUDIT_OUT_DIR || os.tmpdir()

/** 变异体：把**已实现**的分支在打包期改回缺陷版（不改仓库文件，可反复重跑） */
const MUTATIONS = {
  'market-degraded-hidden': {
    file: 'src/pages/MarketPage.tsx',
    // 把「板块数据不可得 → 如实告知 + 重试」降级分支删掉（= 静默什么都不显示）
    from: "return <Unavailable message={q.data?.error ?? '板块行情请求失败'} onRetry={() => void q.refetch()} />",
    to: 'return null /* MUTANT: 降级分支被删 */',
    expect: 'H5/market-sectors-degraded-honest',
  },
  'market-tone-inverted': {
    file: 'src/pages/MarketPage.tsx',
    // 涨跌色写反（涨绿跌红）——A 股口径下是缺陷
    from: "  if (v == null || v === 0) return 'flat'\n  return v > 0 ? 'rise' : 'fall'",
    to: "  if (v == null || v === 0) return 'flat'\n  return v > 0 ? 'fall' : 'rise' /* MUTANT: 涨跌色写反 */",
    expect: 'H5/market-rise-fall-tone',
  },
}
const mutation = MUTATE ? MUTATIONS[MUTATE] : null
if (MUTATE && !mutation) {
  console.error(`[H5-DOM] 未知变异体 --mutate ${MUTATE}（可用：${Object.keys(MUTATIONS).join(', ')}）`)
  process.exit(2)
}

/** rolldown 插件：打包期源码级变异（锚点必须唯一命中，否则直接抛错，不许静默失效） */
function mutationPlugin(mut) {
  return {
    name: 'h5-dom-mutation',
    transform(code, id) {
      const norm = String(id).replace(/\\/g, '/')
      if (!norm.endsWith(mut.file)) return null
      const hits = code.split(mut.from).length - 1
      if (hits !== 1) {
        throw new Error(`变异锚点在 ${mut.file} 命中 ${hits} 次（期望 1 次）：${mut.from}`)
      }
      return { code: code.replace(mut.from, mut.to), map: null }
    },
  }
}

// ==================== ① 打包真组件 ====================
const { rolldown } = requireFromFrontend('rolldown')
const { JSDOM } = requireFromFrontend('jsdom')

const bundleDir = fs.mkdtempSync(path.join(os.tmpdir(), 'h5-dom-bundle-'))
const entryFile = path.join(FRONTEND, 'harness', 'h5_dom_entry.tsx')

async function buildBundle() {
  const build = await rolldown({
    input: entryFile,
    platform: 'node',
    plugins: mutation ? [mutationPlugin(mutation)] : [],
    transform: {
      jsx: 'react-jsx',
      // vite 在构建期替换 import.meta.env；裸 rolldown 不会 ⇒ 显式定义（api.ts 读 VITE_API_BASE）
      define: { 'import.meta.env': '{}' },
    },
  })
  await build.write({ dir: bundleDir, format: 'esm', entryFileNames: 'entry.mjs' })
  return path.join(bundleDir, 'entry.mjs')
}

// ==================== ② jsdom 环境（真 DOM，无浏览器） ====================
const GLOBAL_KEYS = [
  'window', 'document', 'navigator', 'location',
  'HTMLElement', 'HTMLTextAreaElement', 'HTMLInputElement', 'HTMLButtonElement',
  'Element', 'Node', 'SVGElement', 'Event', 'CustomEvent', 'MouseEvent', 'KeyboardEvent',
  'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'MutationObserver',
]

function installDom() {
  const dom = new JSDOM('<!doctype html><html><body><div id="host"></div></body></html>',
    { url: 'http://localhost/', pretendToBeVisual: true })
  for (const key of GLOBAL_KEYS) {
    const value = key === 'window' ? dom.window : dom.window[key]
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true })
  }
  dom.window.Element.prototype.scrollIntoView = function scrollIntoView() {}
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  return dom
}

// ==================== ③ 桩 HTTP（响应体与真后端同构，取自 report-H5.md 实测输出） ====================
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const jsonResponse = (value, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })

// 真后端原样抓取（2026-10-04 实测 /api/market/*、/api/watchlist）
const REAL_INDEX = [
  { name: '上证指数', code: 'sh000001', price: 3842.19, change: 11.74, change_percent: 0.31 },
  { name: '深证成指', code: 'sz399001', price: 12887.62, change: -14.33, change_percent: -0.11 },
  { name: '创业板指', code: 'sz399006', price: 3135.28, change: -7.28, change_percent: -0.23 },
  { name: '沪深300', code: 'sh000300', price: 4357.62, change: 12.41, change_percent: 0.29 },
  { name: '上证50', code: 'sh000016', price: 2823.28, change: 13.16, change_percent: 0.47 },
  { name: '中证500', code: 'sh000905', price: 7435.16, change: -4.47, change_percent: -0.06 },
]
const REAL_VALUATION = [
  { name: '沪深300', code: '000300', pe: 13.18, pe_percentile: 60.6, pb: 1.4, pb_percentile: 33.6, eva_type: '正常', eva_type_int: 1, update_date: '2026-10-04' },
  { name: '上证50', code: '000016', pe: 10.69, pe_percentile: 55.2, pb: 1.23, pb_percentile: 46.8, eva_type: '正常', eva_type_int: 1, update_date: '2026-10-04' },
  { name: '创业板指', code: '399006', pe: 35.83, pe_percentile: 27.5, pb: 5.01, pb_percentile: 53.8, eva_type: '低估', eva_type_int: 0, update_date: '2026-10-04' },
  { name: '中证500', code: '000905', pe: 31.92, pe_percentile: 73.8, pb: 2.31, pb_percentile: 75.2, eva_type: '高估', eva_type_int: 2, update_date: '2026-10-04' },
]

let marketRes = {}
let watchlistDb = []
let holdingsDb = []
let searchResults = []
let fetchCalls = []

function resetStubs() {
  marketRes = {
    index: { ok: true, indices: REAL_INDEX, error: null },
    sectors: { ok: true, sectors: [{ name: '银行', code: 'BK0475', change: 1.5 }], error: null },
    sentiment: {
      ok: true,
      sentiment: {
        limit_up_down: { limit_up: 30, limit_down: 5 }, board_height: 4,
        breadth: { up_count: 3000, down_count: 1800, total: 5000, up_ratio: 60.0 }, errors: [],
      },
      error: null,
    },
    moneyflow: {
      ok: true,
      moneyflow: {
        main_flow: -12.5, super_large_flow: 3.2, large_flow: -15.7, medium_flow: 1.1,
        small_flow: -2.3, north_flow: null, south_flow: 68.6361, update_time: '2026-10-04 00:56',
      },
      sectors: [{ name: '银行', change: 1.2, main_flow: 3.4, main_flow_ratio: 2.1 }],
      error: null,
    },
    valuation: { ok: true, valuation: REAL_VALUATION, error: null },
  }
  watchlistDb = [{
    id: 4, code: '600519', name: '贵州茅台', market: '上海主板',
    added_time: '2026-10-03 16:56:33', price: 1258.62, change: 23.04, change_percent: 1.86,
  }]
  holdingsDb = [{
    id: 1, code: '600519', name: '贵州茅台', quantity: 100, cost_price: 1250,
    price: 1258.62, change_percent: 1.86, market_value: 125862, pnl: 862, pnl_percent: 0.69,
  }]
  searchResults = [{ code: '300750', name: '宁德时代', type: '股票' }]
  fetchCalls = []
}

function installFetch() {
  globalThis.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : String(input?.url ?? input)
    const method = String(init?.method || 'GET').toUpperCase()
    fetchCalls.push(`${method} ${url}`)
    const p = new URL(url, 'http://localhost').pathname
    const body = init?.body ? JSON.parse(String(init.body)) : {}

    if (p.startsWith('/api/market/')) {
      const key = p.slice('/api/market/'.length)
      return jsonResponse(marketRes[key] ?? { ok: false, error: 'unexpected ' + p })
    }
    if (p === '/api/watchlist' && method === 'GET') return jsonResponse({ ok: true, items: watchlistDb })
    if (p === '/api/watchlist/search') return jsonResponse({ ok: true, results: searchResults })
    if (p === '/api/watchlist' && method === 'POST') {
      if (watchlistDb.some((x) => x.code === body.code)) {
        return jsonResponse({ ok: false, error: `${body.code} 已在自选中` })
      }
      watchlistDb = [...watchlistDb, {
        id: 99, code: body.code, name: body.name || '', market: '上海主板', added_time: '',
        price: 291.11, change: 4.31, change_percent: 1.5,
      }]
      return jsonResponse({ ok: true, code: body.code, name: body.name })
    }
    if (p.startsWith('/api/watchlist/') && method === 'DELETE') {
      const code = p.split('/').pop()
      watchlistDb = watchlistDb.filter((x) => x.code !== code)
      return jsonResponse({ ok: true, code })
    }
    if (p === '/api/stocks/holdings' && method === 'GET') return jsonResponse({ ok: true, items: holdingsDb })
    if (p === '/api/stocks/holdings' && method === 'POST') return jsonResponse({ ok: true, code: body.code })
    if (p.startsWith('/api/stocks/holdings/') && method === 'DELETE') return jsonResponse({ ok: true })
    return jsonResponse({ ok: true })
  }
}

// ==================== 断言收集 ====================
const checks = []
function check(name, ok, detail = '') {
  checks.push({ name, ok: Boolean(ok), detail })
  return Boolean(ok)
}
function section(title) {
  console.log(`\n----- ${title} -----`)
}

// ==================== DOM 工具 ====================
let entry = null

async function mountPage(host, mountFn) {
  let mounted = null
  await entry.act(async () => { mounted = mountFn(host) })
  return mounted
}

async function clickText(host, text) {
  const btn = [...host.querySelectorAll('button')].find((b) => b.textContent.trim() === text)
  if (!btn) throw new Error(`未找到按钮「${text}」`)
  await entry.act(async () => { btn.dispatchEvent(new globalThis.MouseEvent('click', { bubbles: true })) })
}

async function typeInput(input, value) {
  const setter = Object.getOwnPropertyDescriptor(globalThis.HTMLInputElement.prototype, 'value').set
  await entry.act(async () => {
    setter.call(input, value)
    input.dispatchEvent(new globalThis.Event('input', { bubbles: true }))
  })
}

async function waitFor(pred, timeoutMs = 5000, stepMs = 20) {
  const t0 = Date.now()
  for (;;) {
    if (pred()) return true
    if (Date.now() - t0 > timeoutMs) return false
    await entry.act(async () => { await sleep(stepMs) })
  }
}

/** 收集 .num 元素的「文本 → tone class」（涨跌色断言用） */
function numTones(host) {
  return [...host.querySelectorAll('span.num')].map((el) => ({
    text: el.textContent.trim(),
    cls: el.className,
  }))
}

function looksFake(text) {
  return /NaN|undefined|Infinity|null/.test(text)
}

// ==================== 场景 A：市场行情五 tab ====================
async function scenarioMarket() {
  section('市场行情 · 指数/板块/情绪/资金/估值 五 tab（真组件 + 桩 /api/market/*）')
  resetStubs()
  const host = document.createElement('div')
  document.body.appendChild(host)
  const { root } = await mountPage(host, entry.mountMarketPage)

  const ready = await waitFor(() => host.textContent.includes('3842.19'))
  const text0 = host.textContent
  check('H5/market-index-rows', ready && text0.includes('上证指数') && text0.includes('+0.31%'),
    `ready=${ready}，含上证指数/3842.19/+0.31%`)
  console.log(`  ${ready ? '✓' : '✗'} 指数 tab 渲染真行情：${text0.includes('3842.19')}`)

  const tones = numTones(host)
  const rise = tones.find((t) => t.text === '+0.31%')
  const fall = tones.find((t) => t.text === '-0.11%')
  const toneOk = Boolean(rise && fall && rise.cls.includes('text-rise') && fall.cls.includes('text-fall'))
  check('H5/market-rise-fall-tone', toneOk,
    `+0.31%→${rise ? rise.cls : 'missing'}；-0.11%→${fall ? fall.cls : 'missing'}（涨红跌绿）`)
  console.log(`  ${toneOk ? '✓' : '✗'} 涨跌色：+0.31%=${rise?.cls} / -0.11%=${fall?.cls}`)

  await clickText(host, '板块')
  await waitFor(() => host.textContent.includes('银行'))
  const secOk = host.textContent.includes('银行') && host.textContent.includes('+1.50%')
  check('H5/market-sectors-rows', secOk, `板块 tab 含 银行/+1.50%`)
  console.log(`  ${secOk ? '✓' : '✗'} 板块 tab：${secOk}`)

  await clickText(host, '情绪')
  await waitFor(() => host.textContent.includes('涨停家数'))
  const t1 = host.textContent
  const sentOk = t1.includes('涨停家数') && t1.includes('30') && t1.includes('4 板') && t1.includes('60.0%')
  check('H5/market-sentiment-rows', sentOk, `情绪 tab 含 涨停家数30 / 4 板 / 上涨占比60.0%`)
  console.log(`  ${sentOk ? '✓' : '✗'} 情绪 tab：${sentOk}`)

  await clickText(host, '资金')
  await waitFor(() => host.textContent.includes('主力净流入'))
  const t2 = host.textContent
  const flowOk = t2.includes('-12.50 亿') && t2.includes('+68.64 亿') && t2.includes('银行') && t2.includes('北向资金')
  check('H5/market-moneyflow-rows', flowOk, `资金 tab 含 主力-12.50 亿 / 南向+68.64 亿 / 板块银行`)
  console.log(`  ${flowOk ? '✓' : '✗'} 资金 tab：${flowOk}`)

  await clickText(host, '估值')
  await waitFor(() => host.textContent.includes('沪深300'))
  const t3 = host.textContent
  const valOk = t3.includes('13.18') && t3.includes('60.6%') && t3.includes('高估') && t3.includes('低估')
  check('H5/market-valuation-rows', valOk, `估值 tab 含 PE13.18 / 分位60.6% / 高估 / 低估`)
  console.log(`  ${valOk ? '✓' : '✗'} 估值 tab：${valOk}`)

  const valRise = [...host.querySelectorAll('td.text-rise')].some((td) => td.textContent.includes('低估'))
  const valFall = [...host.querySelectorAll('td.text-fall')].some((td) => td.textContent.includes('高估'))
  check('H5/market-valuation-tone', valRise && valFall, `低估→text-rise=${valRise}；高估→text-fall=${valFall}`)
  console.log(`  ${valRise && valFall ? '✓' : '✗'} 估值状态色：低估rise=${valRise} 高估fall=${valFall}`)

  check('H5/market-no-fake-tokens', !looksFake(t3), `页面文本不含 NaN/undefined/null 字面量`)
  console.log(`  ${!looksFake(t3) ? '✓' : '✗'} 无 NaN/undefined 假值渲染`)

  await entry.act(async () => { root.unmount() })
  host.remove()
}

// ==================== 场景 B：数据不可得必须如实告知（不占位编造） ====================
async function scenarioMarketDegraded() {
  section('市场行情 · 板块数据不可得 → 如实降级（不编造、不空表冒充）')
  resetStubs()
  marketRes.sectors = { ok: false, sectors: [], error: '板块行情不可得' }
  const host = document.createElement('div')
  document.body.appendChild(host)
  const { root } = await mountPage(host, entry.mountMarketPage)

  await waitFor(() => host.textContent.includes('3842.19'))
  await clickText(host, '板块')
  const shown = await waitFor(() => host.textContent.includes('板块行情不可得'))
  const text = host.textContent
  const honest = shown && text.includes('不做占位编造') && host.querySelectorAll('table').length === 0
  check('H5/market-sectors-degraded-honest', honest,
    `降级文案出现=${shown}，含"不做占位编造"=${text.includes('不做占位编造')}，表格数=${host.querySelectorAll('table').length}（期望 0）`)
  console.log(`  ${honest ? '✓' : '✗'} 降级如实告知：${honest}`)

  await entry.act(async () => { root.unmount() })
  host.remove()
}

// ==================== 场景 C：自选股（列表 + 搜索加入 + 行情降级） ====================
async function scenarioWatchlist() {
  section('自选股 · 列表/搜索加入/持仓 tab（真组件 + 桩 /api/watchlist）')
  resetStubs()
  const host = document.createElement('div')
  document.body.appendChild(host)
  const { root } = await mountPage(host, entry.mountWatchlistPage)

  const ready = await waitFor(() => host.textContent.includes('1258.62'))
  const t = host.textContent
  const listOk = ready && t.includes('贵州茅台') && t.includes('600519') && t.includes('+1.86%')
  check('H5/watchlist-rows', listOk, `自选列表含 贵州茅台/600519/1258.62/+1.86%`)
  console.log(`  ${listOk ? '✓' : '✗'} 自选列表真行情：${listOk}`)

  const input = host.querySelector('input')
  await typeInput(input, '宁德')
  await clickText(host, '搜索')
  const searched = await waitFor(() => host.textContent.includes('加入自选'))
  check('H5/watchlist-search-results', searched && host.textContent.includes('宁德时代'),
    `搜索"宁德"→出现候选「宁德时代」+「加入自选」按钮`)
  console.log(`  ${searched ? '✓' : '✗'} 搜索候选：${searched}`)

  await clickText(host, '加入自选')
  const posted = await waitFor(() => fetchCalls.some((c) => c.startsWith('POST /api/watchlist')))
  const addedRow = await waitFor(() => host.textContent.includes('宁德时代') && host.textContent.includes('291.11'))
  check('H5/watchlist-add-posts', posted && addedRow, `点「加入自选」发出 POST 且列表出现新标的`)
  console.log(`  ${posted && addedRow ? '✓' : '✗'} 加入自选 POST + 回显：${posted && addedRow}`)

  await clickText(host, '📋 持仓股票')
  const holdReady = await waitFor(() => host.textContent.includes('125,862.00'))
  const th = host.textContent
  const holdOk = holdReady && th.includes('+862.00') && th.includes('+0.69%') && th.includes('1250.00')
  check('H5/holdings-market-value-pnl', holdOk, `持仓 tab 含 市值125,862.00 / 浮盈+862.00(+0.69%) / 成本1250.00`)
  console.log(`  ${holdOk ? '✓' : '✗'} 持仓市值与盈亏：${holdOk}`)

  await entry.act(async () => { root.unmount() })
  host.remove()
}

// ==================== 场景 D：自选行情不可得 → "--"（不编造价格） ====================
async function scenarioWatchlistDegraded() {
  section('自选股 · 行情不可得 → 现价/涨跌显示 "--"')
  resetStubs()
  watchlistDb = [{ id: 5, code: '000001', name: '平安银行', market: '深圳主板', added_time: '', price: null, change: null, change_percent: null }]
  const host = document.createElement('div')
  document.body.appendChild(host)
  const { root } = await mountPage(host, entry.mountWatchlistPage)

  await waitFor(() => host.textContent.includes('平安银行'))
  const t = host.textContent
  const ok = t.includes('平安银行') && t.includes('--') && !looksFake(t)
  check('H5/watchlist-quote-degrades-to-dash', ok, `行情为空→"--" 且无 NaN/null 字面量`)
  console.log(`  ${ok ? '✓' : '✗'} 行情降级为 --：${ok}`)

  await entry.act(async () => { root.unmount() })
  host.remove()
}

// ==================== main ====================
async function main() {
  const bundle = await buildBundle()
  console.log(`[H5-DOM] bundle=${bundle}`)
  if (mutation) console.log(`[H5-DOM] ⚠️ 变异体 ${MUTATE}：${mutation.file} 的「${mutation.from.slice(0, 60)}…」→「${mutation.to.slice(0, 40)}…」`)
  installDom()
  installFetch()
  const origError = console.error
  console.error = (...args) => {
    if (typeof args[0] === 'string' && args[0].includes('not wrapped in act')) return
    origError(...args)
  }
  entry = await import(pathToFileURL(bundle).href)

  await scenarioMarket()
  await scenarioMarketDegraded()
  await scenarioWatchlist()
  await scenarioWatchlistDegraded()

  const failed = checks.filter((c) => !c.ok)
  const result = {
    generated_at: new Date().toISOString(),
    mutation: MUTATE || null,
    total: checks.length,
    failed: failed.map((c) => c.name),
    checks,
    fetch_calls: fetchCalls,
  }
  fs.mkdirSync(OUT_DIR, { recursive: true })
  const outPath = path.join(OUT_DIR, `h5-dom-result${MUTATE ? '-' + MUTATE : ''}.json`)
  fs.writeFileSync(outPath, JSON.stringify(result, null, 2), 'utf-8')

  console.log(`\n===== 汇总 =====`)
  console.log(`断言 ${checks.length} 条，失败 ${failed.length} 条`)
  for (const c of failed) console.log(`  ✗ ${c.name} :: ${c.detail}`)
  console.log(`result_json=${outPath}`)

  if (!KEEP) {
    try { fs.rmSync(bundleDir, { recursive: true, force: true }) } catch { /* 临时目录清理失败无妨 */ }
  }

  if (mutation) {
    const caught = failed.some((c) => c.name === mutation.expect)
    console.log(`MUTANT(${MUTATE}) ${caught ? 'CAUGHT ✅（harness 有牙：缺陷版被断言抓住）' : 'SURVIVED ❌（harness 对该缺陷无感，等于没测）'}`)
    return caught ? 0 : 1
  }
  console.log(`RESULT=${failed.length === 0 ? 'ALL_PASS' : 'HAS_FAILURES'}`)
  return failed.length === 0 ? 0 : 1
}

main().then((code) => process.exit(code)).catch((err) => {
  console.error('[H5-DOM] 运行失败：', err)
  process.exit(2)
})
