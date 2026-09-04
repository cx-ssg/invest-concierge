/**
 * API 客户端：基址默认 = 同源相对（''）。
 * - 生产：FastAPI 托管 dist（桌面壳/浏览器模式），同源 /api 直达——后端自动挑端口也不断；
 * - 开发：Vite 5173 走 vite.config 的 /api 代理 → 8000，同样相对可达；
 * - 特殊部署可用 VITE_API_BASE 覆盖（如独立后端域名）。
 */

import type {
  AgentConfigPayload,
  ApiOk,
  ChatBody,
  DiagnosisPayload,
  DiaryEntry,
  DiaryIn,
  FundHolding,
  FundIn,
  HealthPayload,
  NavPayload,
  SessionMessage,
  SessionSummary,
  SettingsPayload,
  SSEEvent,
  StatusPayload,
  SummaryPayload,
} from '../types/api'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

/** 合并外部 AbortSignal（react-query 取消）与超时：先到先 abort */
function mergeSignal(signal: AbortSignal | undefined, timeoutMs?: number): { signal: AbortSignal; cleanup: () => void } {
  const ctrl = new AbortController()
  const timer = timeoutMs ? setTimeout(() => ctrl.abort(), timeoutMs) : null
  const onAbort = () => ctrl.abort()
  if (signal) {
    if (signal.aborted) ctrl.abort()
    else signal.addEventListener('abort', onAbort)
  }
  return {
    signal: ctrl.signal,
    cleanup: () => {
      if (timer) clearTimeout(timer)
      if (signal) signal.removeEventListener('abort', onAbort)
    },
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs?: number,
  outerSignal?: AbortSignal,
): Promise<T> {
  const merged = mergeSignal(outerSignal, timeoutMs)
  try {
    const resp = await fetch(`${API_BASE}${path}`, { ...init, signal: merged.signal })
    if (!resp.ok) {
      let detail = ''
      try {
        const body = (await resp.json()) as { detail?: string }
        detail = body?.detail ?? ''
      } catch {
        /* 非 JSON 错误体忽略 */
      }
      throw new Error(`HTTP ${resp.status}${detail ? `：${detail}` : ''}`)
    }
    return resp.json() as Promise<T>
  } finally {
    merged.cleanup()
  }
}

function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }
}

export const api = {
  // ==================== 系统 ====================
  health: () => request<HealthPayload>('/api/health', { headers: { Accept: 'application/json' } }),
  status: () => request<StatusPayload>('/api/status'),
  nav: () => request<NavPayload>('/api/nav'),
  summary: () => request<SummaryPayload>('/api/dashboard/summary'),

  // ==================== Agent / 会话 ====================
  agent: {
    config: () => request<AgentConfigPayload>('/api/agent/config'),
    sessions: (archived = false) =>
      request<SessionSummary[]>(`/api/agent/sessions?archived=${archived ? 'true' : 'false'}`),
    createSession: (title = '') =>
      request<ApiOk & { session_id?: number }>('/api/agent/sessions', jsonInit('POST', { title })),
    sessionMessages: (id: number) =>
      request<SessionMessage[]>(`/api/agent/sessions/${id}/messages`),
    patchSession: (id: number, action: string, title = '') =>
      request<ApiOk & { pinned?: boolean; archived?: boolean }>(
        `/api/agent/sessions/${id}`,
        jsonInit('PATCH', { action, title }),
      ),
    deleteSession: (id: number) => request<ApiOk>(`/api/agent/sessions/${id}`, jsonInit('DELETE')),
    chatSync: (body: ChatBody) =>
      request<ApiOk & { content?: string; session_id?: number | null }>(
        '/api/agent/chat',
        jsonInit('POST', body),
        180_000,
      ),
  },

  // ==================== 诊断（慢接口：90s 超时 → 前端降级文案） ====================
  diagnosis: (code: string, signal?: AbortSignal) =>
    request<DiagnosisPayload>(`/api/stocks/${code}/diagnosis`, {}, 90_000, signal),

  // ==================== 持仓 ====================
  holdings: {
    list: () => request<FundHolding[]>('/api/holdings/funds'),
    add: (body: FundIn) => request<ApiOk>('/api/holdings/funds', jsonInit('POST', body)),
    update: (code: string, body: Partial<FundIn>) =>
      request<ApiOk>(`/api/holdings/funds/${code}`, jsonInit('PUT', body)),
    remove: (code: string) => request<ApiOk>(`/api/holdings/funds/${code}`, jsonInit('DELETE')),
  },

  // ==================== 日记 ====================
  diary: {
    list: () => request<DiaryEntry[]>('/api/diary'),
    add: (body: DiaryIn) => request<ApiOk & { id?: number }>('/api/diary', jsonInit('POST', body)),
    remove: (id: number) => request<ApiOk>(`/api/diary/${id}`, jsonInit('DELETE')),
  },

  // ==================== 设置 ====================
  settings: {
    get: () => request<SettingsPayload>('/api/settings'),
    setDemo: (enabled: boolean) =>
      request<ApiOk & { demo_mode?: boolean }>('/api/settings/demo', jsonInit('POST', { enabled })),
  },
}

/**
 * SSE 流式 chat（POST /api/agent/chat/stream）。
 * fetch 读流逐块解析 `data: {json}` 事件；服务端 `: ping` 注释行自动忽略。
 * 返回 async generator；调用方用 AbortSignal 取消（服务端 ClientDisconnect 收尾）。
 */
export async function* chatStream(
  body: ChatBody,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent, void, void> {
  const ctrl = new AbortController()
  const onAbort = () => ctrl.abort()
  if (signal) {
    if (signal.aborted) ctrl.abort()
    else signal.addEventListener('abort', onAbort)
  }
  try {
    const resp = await fetch(`${API_BASE}/api/agent/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    })
    if (!resp.ok) {
      let detail = ''
      try {
        const j = (await resp.json()) as { detail?: string }
        detail = j?.detail ?? ''
      } catch {
        /* ignore */
      }
      throw new Error(`HTTP ${resp.status}${detail ? `：${detail}` : ''}`)
    }
    if (!resp.body) throw new Error('浏览器不支持流式响应')

    const reader = resp.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        const dataLine = chunk.split('\n').find((l) => l.startsWith('data: '))
        if (!dataLine) continue // 忽略 : ping 注释行
        try {
          yield JSON.parse(dataLine.slice(6)) as SSEEvent
        } catch {
          /* 半包/脏行丢弃，等下一次事件 */
        }
      }
    }
  } finally {
    if (signal) signal.removeEventListener('abort', onAbort)
  }
}