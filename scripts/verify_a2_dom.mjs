#!/usr/bin/env node
/**
 * A2 **离线 DOM 回归 harness**（任务 A-R1 的 F1 / F2 / F3）—— 不需要浏览器、不需要 LLM、
 * 不需要 Ollama、不需要后端。
 *
 * 为什么存在：BUG-001（新会话第一条回答不显示）的修复只被人工脚本
 * `scripts/verify_a2r2_chat_visible.py`（Edge + 真后端 + 真 LLM）覆盖 ⇒ 把它改回缺陷版
 * `python -m pytest -q` 仍 476 passed（阶段 A 审计 F1 独立复现）。本脚本把**真 `ChatArea`
 * 组件**（`frontend/harness/a2_dom_entry.tsx`，rolldown 打成单文件 bundle）挂进 jsdom，
 * 用**桩 SSE**（stub `fetch` + 真 `chatStream` 解析器）跑断言，接进 CI。
 *
 * 用法：
 *   node scripts/verify_a2_dom.mjs                 # 全绿才算过（退出码 0）
 *   node scripts/verify_a2_dom.mjs --mutate bug001 # 变异自检：把 BUG-001 修复改回缺陷版，
 *                                                  # 断言 harness **必须变红**（证明它有牙）
 * 产物：bundle 落系统临时目录（或 `AUDIT_OUT_DIR`）；结果 JSON 同目录，路径打印在末尾。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'

// 仓库根由**本文件位置**推导（F4：脚本里不得出现本机绝对路径）
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
// B1：可选——把**真实后端 dump 的事件流**（scripts/e2e_judge_dump.py 的产物）灌进同一个
// jsdom harness。合成桩测的是"契约"；真流测的是"真产物能不能渲染"（两者互补）。
const SSE_FILE = argValue('--sse-file')
const OUT_DIR = argValue('--out') || process.env.AUDIT_OUT_DIR || os.tmpdir()

// 变异体：把**已修复**的代码在打包期改回缺陷版（不改仓库文件，可反复重跑）
const MUTATIONS = {
  bug001: {
    file: 'src/features/agent/ChatArea.tsx',
    from: 'live != null && live.sessionId === activeId',
    to: 'phase.sessionId === activeId',
    // BUG-002 采纳新 id 后，新会话用例的 activeId 已等于落库 id ⇒ 不再区分该变异；
    // 「运行起始会话 ≠ 落库会话」的同族用例仍能区分（A-R2 实测见 report）
    expect: 'F1/existing-session-refork',
  },
  bug002: {
    file: 'src/features/agent/ChatArea.tsx',
    // 整块关掉「采纳服务端新会话 id」（= 修复前的行为：既不回写 activeId、也不同步 live.sessionId）
    from: 'if (activeId == null) {',
    to: 'if (false) {',
    expect: 'B002/session-id-written-back',
  },
  'bug002-partial': {
    file: 'src/features/agent/ChatArea.tsx',
    // 只回写归属 / 不同步 live.sessionId —— 论证「为什么两行都要」（设计对照，非缺陷版）
    from: 'setLive((l) => (l ? { ...l, sessionId: sid } : l))',
    to: 'void 0 /* MUTANT: 只回写 activeId，不同步 live.sessionId */',
    expect: 'F1/new-session-answer-visible',
  },
  'judge-uncertain-as-relevant': {
    file: 'src/features/agent/ChatArea.tsx',
    // B1 判官：把「未确认」也渲染成「相关」（= 把不可信结果伪装成 relevant 的缺陷版）
    from: "  if (j.verdict === 'relevant') return 'relevant'",
    to: "  if (j.verdict !== 'irrelevant') return 'relevant' /* MUTANT: uncertain→relevant */",
    expect: 'B1/judge-uncertain-not-relevant',
  },
  'src-id-global': {
    file: 'src/features/agent/ChatArea.tsx',
    // H1：把来源卡 id 从「按消息唯一」退回**全局 `src-{n}`**（= 缺陷版：
    // 历史多条消息同页时 src-1 重复，getElementById 只命中第一条）
    from: 'id={sourceCardId(scope, n)}',
    to: 'id={`src-${n}`}',
    expect: 'H1/source-ids-unique',
  },
}
const mutation = MUTATE ? MUTATIONS[MUTATE] : null
if (MUTATE && !mutation) {
  console.error(`[A2-DOM] 未知变异体 --mutate ${MUTATE}（可用：${Object.keys(MUTATIONS).join(', ')}）`)
  process.exit(2)
}

/** rolldown 插件：在打包期做源码级变异（锚点必须唯一命中，否则直接抛错，不许静默失效）。 */
function mutationPlugin(mut) {
  return {
    name: 'a2-dom-mutation',
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

const bundleDir = fs.mkdtempSync(path.join(os.tmpdir(), 'a2-dom-bundle-'))
const entryFile = path.join(FRONTEND, 'harness', 'a2_dom_entry.tsx')

async function buildBundle() {
  const build = await rolldown({
    input: entryFile,
    platform: 'node',
    plugins: mutation ? [mutationPlugin(mutation)] : [],
    transform: {
      jsx: 'react-jsx',
      // vite 会在构建期替换 import.meta.env；裸 rolldown 不会 ⇒ 显式定义（api.ts 读 VITE_API_BASE）
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
  const dom = new JSDOM(
    '<!doctype html><html><body><div id="host"></div><div id="md"></div></body></html>',
    { url: 'http://localhost/', pretendToBeVisual: true },
  )
  for (const key of GLOBAL_KEYS) {
    const value = key === 'window' ? dom.window : dom.window[key]
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true })
  }
  // jsdom 不实现 scrollIntoView（ChatArea 的自动滚底会调它）⇒ 打桩
  dom.window.Element.prototype.scrollIntoView = function scrollIntoView() {}
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  return dom
}

// ==================== ③ 桩 SSE + 桩 HTTP ====================
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * 桩 SSE：数组 = 原样回放；函数 = 由**服务端语义**生成（`(sid, task, inbound) => events`）。
 * A-R2/BUG-002 起桩后端是**有状态的**：按请求体的 `session_id` 决定「续写已有会话」还是
 * 「新建会话」（与 `utils/agent_memory.ensure_session` 同口径：None/非法 → 新建），
 * 并把 `task` / `done.content` 落进该会话的消息表（`/messages` 读它）。
 * 没有这一步，就测不出「后端被拆成两个会话」与「上一条回答其实在库里」。
 */
let currentSse = []
let sessionsDb = {}
let nextSessionId = 900
let streamRequests = []

function resetBackend() {
  sessionsDb = {}
  nextSessionId = 900
  streamRequests = []
}

function sseBody(events) {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
}

/**
 * B1：把 SSE 响应体拆成**两段**——`done` 及之前立即下发，之后（`evidence_judged`）延迟下发。
 *
 * 为什么必须这样测：桩后端若把整条事件流一次性回放，就测不出「前端在 `done` 之后
 * **仍在消费**流」——那正是 B1 判官标注能否渲染的前提（真实服务端就是这么发的：
 * `tasks-B1` §1.2 要求 `done` 先到、判官结果后到）。把响应体做成流并延迟第二段，
 * 前端若在 `done` 处收流，标注就永远不出现 ⇒ 本场景会红。
 */
function sseStreamBody(events, { delayMs = 40 } = {}) {
  const enc = new TextEncoder()
  const cut = events.findIndex((e) => e.type === 'done')
  const head = cut >= 0 ? events.slice(0, cut + 1) : events
  const tail = cut >= 0 ? events.slice(cut + 1) : []
  return new ReadableStream({
    async start(controller) {
      controller.enqueue(enc.encode(sseBody(head)))
      if (tail.length) {
        await sleep(delayMs)
        controller.enqueue(enc.encode(sseBody(tail)))
      }
      controller.close()
    },
  })
}
function jsonResponse(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
function installFetch() {
  const calls = []
  globalThis.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : String(input?.url ?? input)
    calls.push(`${init?.method || 'GET'} ${url}`)
    if (url.includes('/api/agent/chat/stream')) {
      const body = init?.body ? JSON.parse(String(init.body)) : {}
      const inbound = body.session_id ?? null
      // 服务端语义：合法正整数 → 续写；否则新建（ensure_session）
      const landed = typeof inbound === 'number' && inbound > 0 ? inbound : ++nextSessionId
      const events = typeof currentSse === 'function'
        ? currentSse(landed, String(body.task ?? ''), inbound)
        : currentSse
      const doneEv = [...events].reverse().find((e) => e.type === 'done')
      sessionsDb[landed] = sessionsDb[landed] || []
      sessionsDb[landed].push({ role: 'user', content: String(body.task ?? '') })
      sessionsDb[landed].push({ role: 'assistant', content: String(doneEv?.content ?? '') })
      streamRequests.push({
        inbound_session_id: inbound,
        landed_session_id: landed,
        task: String(body.task ?? ''),
      })
      // B1：含 `evidence_judged` 的流按「done 先到、判官后到」的真实时序下发（见 sseStreamBody）
      const hasJudge = events.some((e) => e.type === 'evidence_judged')
      return new Response(hasJudge ? sseStreamBody(events) : sseBody(events), {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    const msg = url.match(/\/api\/agent\/sessions\/(\d+)\/messages/)
    if (msg) return jsonResponse(sessionsDb[Number(msg[1])] || [])
    if (/\/api\/agent\/sessions(\?|$)/.test(url)) {
      return jsonResponse(
        Object.keys(sessionsDb).map((id) => ({
          id: Number(id), title: '', summary: '', pinned: 0, archived: 0,
          created_at: '', updated_at: '',
        })),
      )
    }
    return jsonResponse({ ok: true })
  }
  return calls
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

// ==================== 场景：ChatArea（真组件 + 桩 SSE） ====================
let entry = null
let fetchCalls = []

const ANSWER = 'A2DOM_ANSWER_MARKER：本回答在 done 事件之后必须仍然留在消息区。'
const QUESTION = 'A2DOM 提问：贵州茅台最近公告说了什么？'

function src(chunkId, rank, title, extra = {}) {
  return {
    rank,
    chunk_id: chunkId,
    title,
    url: `https://example.com/${chunkId}`,
    source: 'notice',
    published_at: '2026-08-15',
    code: '600519',
    is_table: false,
    ...extra,
  }
}

/** 两次 tool_end + 一个 done（`session_id` 覆写为新 id）—— 任务书 F1 指定的桩事件序列。 */
function eventsF1(doneSessionId, answer = ANSWER) {
  return [
    { type: 'status', state: 'thinking' },
    { type: 'reasoning', text: '先检索公告，再看行情。' },
    { type: 'tool_start', name: 'retrieve_docs', arguments: { query: '茅台 公告' } },
    { type: 'tool_end', name: 'retrieve_docs', ok: true, elapsed_ms: 12,
      sources: [src(101, 1, 'CALL1-TOP')], evidence_level: 'weak' },
    { type: 'tool_start', name: 'search_stock', arguments: { code: '600519' } },
    { type: 'tool_end', name: 'search_stock', ok: true, elapsed_ms: 30 },
    { type: 'done', session_id: doneSessionId, content: answer, tool_trace: [] },
  ]
}

/** F2：三次检索 —— call1(2 条) / call2(1 条) / call3(1 条重复 + 1 条新)。 */
function eventsF2(doneSessionId, answer) {
  return [
    { type: 'status', state: 'thinking' },
    { type: 'tool_start', name: 'retrieve_docs', arguments: { query: '第一轮' } },
    { type: 'tool_end', name: 'retrieve_docs', ok: true, elapsed_ms: 10,
      sources: [src(101, 1, 'CALL1-TOP'), src(102, 2, 'CALL1-2')], evidence_level: 'weak' },
    { type: 'tool_start', name: 'retrieve_docs', arguments: { query: '第二轮' } },
    { type: 'tool_end', name: 'retrieve_docs', ok: true, elapsed_ms: 11,
      sources: [src(201, 1, 'CALL2-TOP')], evidence_level: 'weak' },
    { type: 'tool_start', name: 'retrieve_docs', arguments: { query: '第三轮' } },
    { type: 'tool_end', name: 'retrieve_docs', ok: true, elapsed_ms: 12,
      sources: [src(101, 1, 'CALL1-TOP'), src(301, 2, 'CALL3-NEW')], evidence_level: 'weak' },
    { type: 'done', session_id: doneSessionId, content: answer, tool_trace: [] },
  ]
}

async function ask(host, text) {
  const ta = host.querySelector('textarea')
  if (!ta) throw new Error('未找到 textarea（ChatArea 没挂上？）')
  const setter = Object.getOwnPropertyDescriptor(
    globalThis.HTMLTextAreaElement.prototype, 'value',
  ).set
  await entry.act(async () => {
    setter.call(ta, text)
    ta.dispatchEvent(new globalThis.Event('input', { bubbles: true }))
  })
  const button = [...host.querySelectorAll('button')].find(
    (b) => b.textContent.trim() === '发送',
  )
  if (!button) throw new Error('未找到「发送」按钮')
  if (button.disabled) throw new Error('「发送」按钮 disabled（输入没进 state？）')
  await entry.act(async () => {
    button.dispatchEvent(new globalThis.MouseEvent('click', { bubbles: true }))
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

async function runChatScenario({ key, activeId, events, answer = ANSWER, readyJudgeCount = 0 }) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  resetBackend()
  currentSse = events
  let mounted = null
  // ⚠️ jsdom 没有 innerText，一律用 textContent（参与断言的只是可见文本语义）
  await entry.act(async () => {
    mounted = entry.mountChatArea(host, {
      activeId,
      apiKeyConfigured: true,
      demoMode: false,
      models: { chat: 'stub-chat', reasoner: 'stub-reasoner' },
    })
  })
  const { root } = mounted
  await ask(host, QUESTION)
  // `answer` 为空 = 调用方不关心正文可见性（真流场景的正文很长，只等判官标注）
  const visible = answer ? await waitFor(() => host.textContent.includes(answer)) : null
  // B1：判官事件**迟于** done 才到（sseStreamBody 延迟第二段）⇒ 必须等它落地再读 DOM
  if (readyJudgeCount > 0) {
    await waitFor(() => host.querySelectorAll('[data-judge]').length >= readyJudgeCount)
  }
  const text = host.textContent
  // H1：来源卡 id = `src-{scope}-{n}`（按消息唯一）。这里同时解析出 scope/n，
  // 断言只关心「编号 n」与「文本」，不再把 scope 写死（运行 scope = `live-{runId}`）。
  const cards = [...host.querySelectorAll('[id^="src-"]')].map((el) => {
    const m = /^src-(.+)-(\d+)$/.exec(el.id)
    return {
      id: el.id,
      scope: m ? m[1] : '',
      n: m ? Number(m[2]) : null,
      title: (el.querySelector('span.flex-1')?.textContent || el.textContent).trim(),
      // B1：判官标注（机器可读档位 + 文案）；无标注 = null（≠ 无关）
      judge: el.querySelector('[data-judge]')?.getAttribute('data-judge') ?? null,
      judgeText: el.querySelector('[data-judge]')?.textContent?.trim() ?? null,
    }
  })
  const sups = [...host.querySelectorAll('sup button')].map((b) => b.textContent.trim())
  const hrefs = [...host.querySelectorAll('a[href]')].map((a) => a.getAttribute('href') || '')
  await entry.act(async () => { root.unmount() })
  host.remove()
  return { key, visible, text, cards, sups, hrefs }
}

async function scenarioF1() {
  section('F1 · BUG-001 新会话回答可见性（两次 tool_end + done 覆写 session_id）')
  const cases = [
    { key: 'F1/new-session-answer-visible', activeId: null, doneSessionId: 901,
      label: 'activeId=null（新会话，done 带回新 id 901）' },
    { key: 'F1/control-existing-session-continue', activeId: 118, doneSessionId: 118,
      label: '对照组 activeId=118（done 仍回 118，正常续写）' },
    { key: 'F1/existing-session-refork', activeId: 118, doneSessionId: 902,
      label: 'activeId=118 但 done 回新 id 902（同族：运行起始会话 ≠ 落库会话）' },
  ]
  for (const c of cases) {
    const r = await runChatScenario({ ...c, events: eventsF1(c.doneSessionId) })
    check(c.key, r.visible, `answerVisibleAtEnd=${r.visible}（${c.label}）`)
    console.log(`  ${r.visible ? '✓' : '✗'} ${c.key}  answerVisibleAtEnd=${r.visible}  [${c.label}]`)
    if (!r.visible) {
      console.log(`      DOM 片段：${JSON.stringify(r.text.slice(0, 160))}`)
    }
  }
}

// ==================== 场景：BUG-002 新会话第二条消息（会话归属回写） ====================
const B002_Q1 = 'BUG002 提问一：贵州茅台最近公告说了什么？'
const B002_A1 = 'BUG002_ANSWER_1：第一条回答——发第 2 条之后必须仍在消息区。'
const B002_Q2 = 'BUG002 提问二：那它的估值分位呢？'
const B002_A2 = 'BUG002_ANSWER_2：第二条回答——必须出现。'

/** 桩后端事件：`sid` 是**服务端落库**的会话 id（新建或续写，由桩后端按请求体判定）。 */
function eventsBug002(sid, task) {
  const answer = task.includes('提问二') ? B002_A2 : B002_A1
  return [
    { type: 'status', state: 'thinking' },
    { type: 'reasoning', text: '先看公告，再看估值分位。' },
    { type: 'tool_start', name: 'search_stock', arguments: { code: '600519' } },
    { type: 'tool_end', name: 'search_stock', ok: true, elapsed_ms: 9 },
    { type: 'done', session_id: sid, content: answer, tool_trace: [] },
  ]
}

/** 同一页面连发两条（第 1 条落地后才发第 2 条），返回两条的可见性与服务端实况。 */
async function twoTurns(activeId) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  let mounted = null
  await entry.act(async () => {
    mounted = entry.mountChatArea(host, {
      activeId,
      apiKeyConfigured: true,
      demoMode: false,
      models: { chat: 'stub-chat', reasoner: 'stub-reasoner' },
    })
  })
  const { root, getActiveId } = mounted

  await ask(host, B002_Q1)
  const firstVisible = await waitFor(() => host.textContent.includes(B002_A1))
  const activeAfterFirst = getActiveId()
  const headerAfterFirst = host.textContent.match(/会话(（新）| #\d+)/)?.[0] || ''

  await ask(host, B002_Q2)
  const secondVisible = await waitFor(() => host.textContent.includes(B002_A2))
  const firstStillVisible = host.textContent.includes(B002_A1)
  const text = host.textContent

  await entry.act(async () => { root.unmount() })
  host.remove()
  return {
    firstVisible, secondVisible, firstStillVisible, text,
    activeAfterFirst, activeBefore: activeId, headerAfterFirst,
    sessions: Object.keys(sessionsDb).map(Number),
    requests: streamRequests.map((r) => ({ ...r })),
  }
}

async function scenarioBug002() {
  section('BUG-002 · 新会话连发两条（会话归属回写 / 不分裂 / 上一条回答仍在）')
  resetBackend()
  currentSse = eventsBug002
  const r = await twoTurns(null)

  check('B002/first-answer-visible', r.firstVisible,
    `第 1 条回答可见=${r.firstVisible}`)
  console.log(`  ${r.firstVisible ? '✓' : '✗'} 第 1 条回答可见 = ${r.firstVisible}`)

  check('B002/session-id-written-back', r.activeAfterFirst === 901,
    `第 1 条 done 后 activeId=${r.activeAfterFirst}（期望 901：服务端新建的会话 id 必须回写归属）`)
  console.log(`  ${r.activeAfterFirst === 901 ? '✓' : '✗'} 会话归属回写：activeId=${r.activeAfterFirst}（期望 901）`)

  check('B002/header-shows-session', r.headerAfterFirst === '会话 #901',
    `头部=${r.headerAfterFirst}（期望 会话 #901）`)
  console.log(`  ${r.headerAfterFirst === '会话 #901' ? '✓' : '✗'} 头部归属文案：${r.headerAfterFirst}（期望 会话 #901）`)

  check('B002/second-answer-visible', r.secondVisible,
    `第 2 条回答可见=${r.secondVisible}`)
  console.log(`  ${r.secondVisible ? '✓' : '✗'} 第 2 条回答可见 = ${r.secondVisible}`)

  check('B002/first-answer-still-visible', r.firstStillVisible,
    `第 2 条落地后第 1 条回答仍在=${r.firstStillVisible}`)
  console.log(`  ${r.firstStillVisible ? '✓' : '✗'} 第 1 条回答仍在 = ${r.firstStillVisible}`)

  const req1 = r.requests[0] || {}
  const req2 = r.requests[1] || {}
  const continued = req2.inbound_session_id != null && req2.inbound_session_id === r.activeAfterFirst
  check('B002/second-run-continues-session', continued,
    `第 2 次请求 session_id=${req2.inbound_session_id}（期望 ${r.activeAfterFirst}，非 null；` +
    '值为 null = 归属没回写，后端会另建会话）')
  console.log(`  ${continued ? '✓' : '✗'} 第 2 次请求续写同一会话：session_id=${req2.inbound_session_id}` +
    `（期望 ${r.activeAfterFirst}，非 null）`)

  const noSplit = r.sessions.length === 1
  check('B002/no-session-split', noSplit,
    `服务端会话=${JSON.stringify(r.sessions)}（期望 1 个：req1(in=${req1.inbound_session_id}→landed=${req1.landed_session_id}) req2(in=${req2.inbound_session_id}→landed=${req2.landed_session_id})）`)
  console.log(`  ${noSplit ? '✓' : '✗'} 服务端会话数=${r.sessions.length} ${JSON.stringify(r.sessions)}（期望 1）`)

  // —— 对照组：activeId 非空（已有会话）时行为不得变化 ——
  resetBackend()
  currentSse = eventsBug002
  const c = await twoTurns(118)
  const ctrlOk = c.firstVisible && c.secondVisible && c.firstStillVisible &&
    c.activeAfterFirst === 118 &&
    c.requests.every((x) => x.inbound_session_id === 118) && c.sessions.length === 1
  check('B002/control-existing-session-unchanged', ctrlOk,
    `对照 activeId=118：两条可见=${c.firstVisible}/${c.secondVisible} 上一条仍在=${c.firstStillVisible} ` +
    `activeId=${c.activeAfterFirst} 请求=${JSON.stringify(c.requests.map((x) => x.inbound_session_id))} 会话=${JSON.stringify(c.sessions)}`)
  console.log(`  ${ctrlOk ? '✓' : '✗'} 对照组 activeId=118 行为不变：两条可见=${c.firstVisible}/${c.secondVisible} 上一条仍在=${c.firstStillVisible} activeId=${c.activeAfterFirst} 请求=${JSON.stringify(c.requests.map((x) => x.inbound_session_id))}`)
  if (!ctrlOk) console.log(`      DOM=${JSON.stringify(c.text.slice(0, 200))}`)
  if (!r.firstStillVisible) console.log(`      B002 DOM=${JSON.stringify(r.text.slice(0, 220))}`)
}

// ==================== 场景：B1 LLM 判官标注（task-B1.md §1.2） ====================
const B1_ANSWER = 'B1JUDGE_ANSWER：判官结论在 done 之后到达，来源卡必须标注。'

/**
 * 桩事件：`done` 之后才发 `evidence_judged`（真实服务端时序，见 sseStreamBody）。
 *
 * 覆盖五种落点（`data-judge` 档位 → 文案）：
 *  101 relevant              → relevant / 判官：相关
 *  102 irrelevant            → irrelevant / 判官：无关
 *  103 uncertain（未给引文） → uncertain / 判官：未确认   ← 「不得说成相关」
 *  104 无结论（模型漏答）    → null / 不标注             ← 沉默 ≠ 无关
 *  105 relevant+quote_rejected → rejected / 判官：未确认（引文未通过校验）
 */
function eventsB1() {
  return [
    { type: 'status', state: 'thinking' },
    { type: 'tool_start', name: 'retrieve_docs', arguments: { query: '茅台 公告' } },
    { type: 'tool_end', name: 'retrieve_docs', ok: true, elapsed_ms: 21, evidence_level: 'weak',
      sources: [
        src(101, 1, 'B1-RELEVANT'),
        src(102, 2, 'B1-IRRELEVANT'),
        src(103, 3, 'B1-UNCERTAIN'),
        src(104, 4, 'B1-NO-VERDICT'),
        src(105, 5, 'B1-QUOTE-REJECTED'),
      ] },
    { type: 'done', session_id: 905, content: B1_ANSWER, tool_trace: [] },
    { type: 'evidence_judged', query: '茅台 公告', level: 'relevant', checked: true, latency_ms: 830,
      reason: '',
      items: [
        { chunk_id: 101, verdict: 'relevant', quote: '贵州茅台上半年营业收入同比增长', quote_rejected: false },
        { chunk_id: 102, verdict: 'irrelevant', quote: '', quote_rejected: false },
        { chunk_id: 103, verdict: 'uncertain', quote: '', quote_rejected: false },
        { chunk_id: 105, verdict: 'relevant', quote: '这段引文其实不存在', quote_rejected: true },
      ] },
  ]
}

async function scenarioB1() {
  section('B1 · LLM 判官标注（done 之后到达；未确认不得显示为相关）')
  const r = await runChatScenario({
    key: 'B1', activeId: null, events: eventsB1(), answer: B1_ANSWER, readyJudgeCount: 4,
  })
  const byN = Object.fromEntries(r.cards.map((c) => [String(c.n), c]))
  const expect = [
    ['1', 'relevant', '判官：相关'],
    ['2', 'irrelevant', '判官：无关'],
    ['3', 'uncertain', '判官：未确认'],
    ['4', null, null],
    ['5', 'rejected', '判官：未确认（引文未通过校验）'],
  ]
  for (const [n, state, label] of expect) {
    const c = byN[n] || {}
    const ok = (c.judge ?? null) === state && (c.judgeText ?? null) === label
    check(`B1/judge-src-${n}`, ok, `src-#${n}(${c.id ?? '-'}) 判官档位=${c.judge} 文案=${c.judgeText}（期望 ${state} / ${label}）`)
    console.log(`  ${ok ? '✓' : '✗'} src-#${n} judge=${c.judge} text=${JSON.stringify(c.judgeText)}（期望 ${state} / ${label}）`)
  }
  // 关键安全断言：未确认 / 引文被拒 的卡片**都不许**出现"相关"字样
  const unsafe = ['3', '5'].filter((n) => (byN[n]?.judgeText || '').includes('相关'))
  check('B1/judge-uncertain-not-relevant', unsafe.length === 0,
    `未确认/引文被拒却被标成"相关"：${JSON.stringify(unsafe)}`)
  console.log(`  ${unsafe.length === 0 ? '✓' : '✗'} 未确认/引文被拒的卡片标成"相关"的数量=${unsafe.length}（期望 0）`)
  if (!r.visible) console.log(`      DOM=${JSON.stringify(r.text.slice(0, 200))}`)
}

/**
 * B1-real：把**真实后端 dump 的事件流**灌进**真 ChatArea**。
 *
 * 合成桩（`eventsB1`）测的是"契约"；本场景测的是"**真产物**能不能渲染" ——
 * 事件来自 `scripts/e2e_judge_dump.py` 的真跑（真 LLM + 真 `retrieve_docs` + 真 SSE）。
 * 判据全部由**流自身**推导（不写死期望值）：标注条数、文案是否落在四态契约内、
 * 未确认/引文被拒**不得**显示成"相关"、来源卡数量与 `tool_end.sources` 一致。
 */
async function scenarioB1RealStream(file) {
  section(`B1-real · 真实 SSE 事件流 → 真组件（${path.basename(file)}）`)
  const raw = JSON.parse(fs.readFileSync(file, 'utf-8'))
  let events = []
  if (Array.isArray(raw)) events = raw
  else if (Array.isArray(raw.runs) && raw.runs[0]?.judge_on?.events) events = raw.runs[0].judge_on.events
  else if (raw.judge_on?.events) events = raw.judge_on.events
  // dump 里每个事件带 `t`（相对时刻）；前端协议不认它，去掉以免污染断言
  events = events.map(({ t, ...rest }) => rest)

  const judged = events.find((e) => e.type === 'evidence_judged')
  const toolEnd = events.find((e) => e.type === 'tool_end' && (e.sources || []).length)
  if (!events.length || !judged || !toolEnd) {
    check('B1real/judge-event-present', false,
      `真实流缺要素（events=${events.length} judged=${!!judged} toolEnd=${!!toolEnd}）`)
    console.log(`  ✗ 真实流缺要素：events=${events.length} judged=${!!judged} toolEnd=${!!toolEnd}`)
    return
  }
  const nItems = (judged.items || []).length
  const r = await runChatScenario({
    key: 'B1real', activeId: null, events, answer: null, readyJudgeCount: 1,
  })
  const cards = r.cards.filter((c) => c.judge)
  const labels = [...new Set(cards.map((c) => c.judgeText))]
  const known = ['判官：相关', '判官：无关', '判官：未确认', '判官：未确认（引文未通过校验）']
  const badLabel = labels.filter((l) => !known.includes(l))
  const unsafe = cards.filter((c) => ['uncertain', 'rejected'].includes(c.judge)
    && (c.judgeText || '').includes('相关'))
  const srcOk = r.cards.length === (toolEnd.sources || []).length

  check('B1real/annotation-rendered', cards.length >= 1,
    `真实事件流（checked=${judged.checked} level=${judged.level} items=${nItems}）渲染出 ${cards.length} 条判官标注`)
  console.log(`  ${cards.length >= 1 ? '✓' : '✗'} 真流渲染出判官标注 ${cards.length} 条（items=${nItems}）`)
  check('B1real/labels-in-contract', badLabel.length === 0,
    `越界文案=${JSON.stringify(badLabel)}（实际 ${JSON.stringify(labels)}）`)
  console.log(`  ${badLabel.length === 0 ? '✓' : '✗'} 文案四态契约：${JSON.stringify(labels)}`)
  check('B1real/no-uncertain-as-relevant', unsafe.length === 0,
    `未确认/引文被拒却显示"相关"：${JSON.stringify(unsafe.map((c) => c.id))}`)
  console.log(`  ${unsafe.length === 0 ? '✓' : '✗'} 未确认/引文被拒显示为"相关"的卡片数=${unsafe.length}`)
  check('B1real/source-cards-match-tool-end', srcOk,
    `来源卡=${r.cards.length} vs tool_end.sources=${(toolEnd.sources || []).length}`)
  console.log(`  ${srcOk ? '✓' : '✗'} 来源卡数=${r.cards.length}（tool_end.sources=${(toolEnd.sources || []).length}）`)
  for (const c of cards) {
    console.log(`      ${c.id} judge=${c.judge} text=${JSON.stringify(c.judgeText)}`)
  }
}

async function scenarioF2() {
  section('F2 · 多次检索的 [n] ↔ 来源卡编号（注入侧全局编号 × 前端合并去重）')
  const answer = '结论见 [1] 与 [3]，第三轮新增见 [4]。'
  const r = await runChatScenario({
    key: 'F2', activeId: null, events: eventsF2(903, answer), answer,
  })
  const expectCards = ['1:CALL1-TOP', '2:CALL1-2', '3:CALL2-TOP', '4:CALL3-NEW']
  const got = r.cards.map((c) => `${c.n}:${c.title.replace(/\s+/g, '')}`)
  check('F2/card-order-dedup-first-seen', got.join(' | ') === expectCards.join(' | '),
    `cards=${got.join(' | ')}（期望 ${expectCards.join(' | ')}：chunk101 去重后仍为 #1）`)
  console.log(`  ${got.join(' | ') === expectCards.join(' | ') ? '✓' : '✗'} 卡片顺序/去重：${got.join(' | ')}`)

  const supOk = ['[1]', '[3]', '[4]'].every((s) => r.sups.includes(s))
  check('F2/body-superscripts', supOk, `正文上标=${JSON.stringify(r.sups)}（期望含 [1] [3] [4]）`)
  console.log(`  ${supOk ? '✓' : '✗'} 正文上标：${JSON.stringify(r.sups)}`)

  // 「正文里的 [n] ↔ 卡片编号」对应关系：n 号卡片必须就是注入侧给该 chunk 的全局编号所指的那条
  const byN = Object.fromEntries(r.cards.map((c) => [String(c.n), c.title.replace(/\s+/g, '')]))
  const pairs = [['[1]', '1', 'CALL1-TOP'], ['[3]', '3', 'CALL2-TOP'], ['[4]', '4', 'CALL3-NEW']]
  for (const [sup, n, title] of pairs) {
    const ok = r.sups.includes(sup) && byN[n] === title
    check(`F2/mapping-${sup}`, ok, `${sup} → src-#${n}(${byN[n]}) 期望 ${title}`)
    console.log(`  ${ok ? '✓' : '✗'} ${sup} → src-#${n} = ${byN[n]}（期望 ${title}）`)
  }
}

// ==================== 场景：H1 来源卡 id 按消息唯一 ====================

const H1_SRCS = (tag) => [1, 2, 3, 4, 5].map((k) => src(9000 + k, k, `${tag}-SRC-${k}`))

/**
 * H1（`task-H1.md` §4）回归锁：**历史两条消息各 5 条来源**。
 *
 * 缺陷形态（旧代码）：来源卡 id 是全局 `src-{n}` ⇒ 同页两条消息各有一个 `src-1`，
 * `document.getElementById('src-1')` 只命中**文档里第一条** ⇒ 点第 2 条消息正文的 `[1]`
 * 会滚到第 1 条消息的位置。本场景两条都断言：id 全唯一 + 点击落在**本条消息**的卡片上。
 */
async function scenarioH1() {
  section('H1 · 来源卡 id 按消息唯一（src-{scope}-{n}；两条消息各 5 条来源）')
  const host = document.createElement('div')
  document.body.appendChild(host)
  resetBackend()
  sessionsDb[901] = [
    { role: 'user', content: 'H1 提问一' },
    { role: 'assistant', content: 'H1 回答一，见 [1] 与 [5]。', sources: H1_SRCS('MSG1') },
    { role: 'user', content: 'H1 提问二' },
    { role: 'assistant', content: 'H1 回答二，见 [1] 与 [5]。', sources: H1_SRCS('MSG2') },
  ]
  let mounted = null
  await entry.act(async () => {
    mounted = entry.mountChatArea(host, {
      activeId: 901,
      apiKeyConfigured: true,
      demoMode: false,
      models: { chat: 'stub-chat', reasoner: 'stub-reasoner' },
    })
  })
  await waitFor(() => host.querySelectorAll('[id^="src-"]').length >= 10)
  const ids = [...host.querySelectorAll('[id^="src-"]')].map((el) => el.id)
  const uniq = new Set(ids)
  const allUnique = ids.length === 10 && uniq.size === 10
  check('H1/source-ids-unique', allUnique,
    `来源卡 ${ids.length} 张 / 唯一 id ${uniq.size} 个（期望 10/10）；ids=${JSON.stringify(ids)}`)
  console.log(`  ${allUnique ? '✓' : '✗'} id 唯一性：${ids.length} 张卡 / ${uniq.size} 个唯一 id`)
  if (!allUnique) console.log(`      ids=${JSON.stringify(ids)}`)

  const wrappers = [...host.querySelectorAll('[data-msg-scope]')]
  const scopes = wrappers.map((w) => w.getAttribute('data-msg-scope'))
  const distinct = scopes.length === 2 && scopes[0] !== scopes[1]
  check('H1/two-distinct-scopes', distinct,
    `消息容器 scope=${JSON.stringify(scopes)}（期望 2 个互不相同）`)
  console.log(`  ${distinct ? '✓' : '✗'} 两条消息的 scope：${JSON.stringify(scopes)}`)

  // 行为锁：点「第 2 条消息」正文的 [1] → 滚动/高亮目标必须落在**第 2 条消息**内部
  const scrolled = []
  const origScroll = globalThis.Element.prototype.scrollIntoView
  globalThis.Element.prototype.scrollIntoView = function recordScroll() { scrolled.push(this) }
  const w2 = wrappers[1] || null
  const btn = w2 ? w2.querySelector('sup button') : null
  if (btn) {
    await entry.act(async () => {
      btn.dispatchEvent(new globalThis.MouseEvent('click', { bubbles: true }))
    })
  }
  globalThis.Element.prototype.scrollIntoView = origScroll
  const target = scrolled[0] || null
  const own = Boolean(w2 && target && w2.contains(target))
    && Boolean(target && target.classList.contains('src-flash'))
  check('H1/jump-targets-own-message', own,
    `点击第 2 条消息的 [1] → 落点 ${target ? target.id : 'null'}（期望在本消息内且带 src-flash；` +
    `w2.scope=${w2 ? w2.getAttribute('data-msg-scope') : '-'}）`)
  console.log(`  ${own ? '✓' : '✗'} 点击第 2 条消息 [1] → ${target ? target.id : 'null'}（期望落在本消息内）`)

  await entry.act(async () => { mounted.root.unmount() })
  host.remove()
}

// ==================== 场景：MarkdownContent 代码块保护（F3） ====================
const MD_CASES = [
  { key: 'closed-fence', content: '```python\narr[1] = 2\n```', corrupt: '](#src-1)' },
  { key: 'unclosed-tilde-fence', content: '~~~\narr[1] = 2\n', corrupt: '](#src-1)' },
  { key: 'indented-4sp', content: '    arr[1] = 2', corrupt: '](#src-1)' },
  { key: 'inline-code', content: '`arr[1] = 2`', corrupt: '](#src-1)' },
]

/** 在独立容器里挂真 `MarkdownContent` 并等 React 落盘（render 也要包 act）。 */
async function renderMarkdownIn(content, sources) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  let root = null
  await entry.act(async () => { root = entry.renderMarkdown(host, content, sources) })
  await entry.act(async () => { await sleep(0) })
  return { host, root }
}

async function scenarioF3() {
  section('F3 · MarkdownContent 代码块保护（闭合围栏 / 未闭合 ~~~ / 4 空格缩进 / 行内 code）')
  const sources = [src(101, 1, 'SRC-1')]
  for (const c of MD_CASES) {
    const { host, root } = await renderMarkdownIn(c.content, sources)
    const html = host.innerHTML
    const corrupted = html.includes(c.corrupt)
    const kept = html.includes('arr[1] = 2')
    check(`F3/${c.key}`, !corrupted && kept,
      `代码块原样保留=${kept} 被替换污染=${corrupted} html=${JSON.stringify(html.slice(0, 120))}`)
    console.log(`  ${!corrupted && kept ? '✓' : '✗'} ${c.key.padEnd(22)} 污染=${corrupted} html=${JSON.stringify(html.slice(0, 90))}`)
    await entry.act(async () => { root.unmount() })
    host.remove()
  }
  // 正对照：码外的 [1] 必须仍被链接化（防止「一刀切不替换」把功能修没了）
  {
    const { host, root } = await renderMarkdownIn('结论见 [1]。', sources)
    const supCount = host.querySelectorAll('sup button').length
    check('F3/positive-control-body-citation', supCount === 1, `正文 [1] 上标数=${supCount}（期望 1）`)
    console.log(`  ${supCount === 1 ? '✓' : '✗'} positive-control      body sup count=${supCount}`)
    await entry.act(async () => { root.unmount() })
    host.remove()
  }
  // 越界编号不得造死链（既有行为，零回归）
  {
    const { host, root } = await renderMarkdownIn('见 [9]。', sources)
    const sup2 = host.querySelectorAll('sup button').length
    check('F3/out-of-range-kept-plain', sup2 === 0, `[9] 上标数=${sup2}（期望 0）`)
    console.log(`  ${sup2 === 0 ? '✓' : '✗'} out-of-range          [9] sup count=${sup2}`)
    await entry.act(async () => { root.unmount() })
    host.remove()
  }
}

// ==================== 场景：来源卡 scheme 白名单（F5 渲染层） ====================
async function scenarioF5() {
  section('F5 · 来源卡外链 scheme 白名单（渲染层兜底）')
  const events = [
    { type: 'status', state: 'thinking' },
    { type: 'tool_start', name: 'retrieve_docs', arguments: { query: 'x' } },
    { type: 'tool_end', name: 'retrieve_docs', ok: true, elapsed_ms: 5, evidence_level: 'weak',
      sources: [
        src(101, 1, 'SAFE-TOP'),
        src(102, 2, 'XSS-TOP', { url: "javascript:alert('audit-xss')" }),
        src(103, 3, 'DATA-TOP', { url: 'data:text/html,<script>1</script>' }),
      ] },
    { type: 'done', session_id: 904, content: '带不可信 url 的来源卡渲染检查。', tool_trace: [] },
  ]
  const r = await runChatScenario({ key: 'F5', activeId: null, events, answer: '带不可信 url 的来源卡渲染检查。' })
  const bad = r.hrefs.filter((h) => /^\s*(javascript|data):/i.test(h))
  const safeKept = r.hrefs.some((h) => h.startsWith('https://example.com/'))
  check('F5/no-dangerous-scheme-anchor', bad.length === 0 && safeKept,
    `危险 scheme 外链数=${bad.length}（期望 0）；https 外链保留=${safeKept}；卡片数=${r.cards.length}`)
  console.log(`  ${bad.length === 0 && safeKept ? '✓' : '✗'} 危险 scheme 外链数=${bad.length} https 外链保留=${safeKept}`)
  console.log(`      hrefs=${JSON.stringify(r.hrefs)}`)
}

// ==================== main ====================
async function main() {
  const bundle = await buildBundle()
  console.log(`[A2-DOM] bundle=${bundle}`)
  if (mutation) console.log(`[A2-DOM] ⚠️ 变异体 ${MUTATE}：${mutation.file} 的「${mutation.from}」→「${mutation.to}」`)
  installDom()
  fetchCalls = installFetch()
  // React 的 act 警告在「SSE 还在推事件」时避不开；过滤掉，别淹掉真正的失败输出
  const origError = console.error
  console.error = (...args) => {
    if (typeof args[0] === 'string' && args[0].includes('not wrapped in act')) return
    origError(...args)
  }
  entry = await import(pathToFileURL(bundle).href)

  await scenarioF1()
  await scenarioBug002()
  await scenarioF2()
  await scenarioF3()
  await scenarioF5()
  await scenarioH1()
  await scenarioB1()
  if (SSE_FILE) await scenarioB1RealStream(SSE_FILE)

  const failed = checks.filter((c) => !c.ok)
  const result = {
    generated_at: new Date().toISOString(),
    mutation: MUTATE || null,
    total: checks.length,
    failed: failed.map((c) => c.name),
    checks,
    fetch_calls: fetchCalls,
    stream_requests: streamRequests,
  }
  fs.mkdirSync(OUT_DIR, { recursive: true })
  const outPath = path.join(OUT_DIR, `a2-dom-result${MUTATE ? '-' + MUTATE : ''}.json`)
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
  console.error('[A2-DOM] 运行失败：', err)
  process.exit(2)
})

