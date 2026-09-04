/** 数字/日期格式化工具（对齐 pages/stock_diagnosis.py 的 _fmt_num/_fmt_pct/_fmt_yi 口径） */

export function toNum(value: unknown): number | null {
  if (value === null || value === undefined) return null
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return null
  return n
}

/** None / NaN → '--'；否则保留 digits 位小数 */
export function fmtNum(value: unknown, digits = 2): string {
  const n = toNum(value)
  if (n === null) return '--'
  return n.toFixed(digits)
}

export function fmtPct(value: unknown, digits = 2): string {
  const n = toNum(value)
  if (n === null) return '--'
  return `${n.toFixed(digits)}%`
}

/** 元 → 亿 格式化（大额财务口径） */
export function fmtYi(value: unknown, digits = 2): string {
  const n = toNum(value)
  if (n === null) return '--'
  return `${(n / 100000000).toFixed(digits)}亿`
}

/** 万/亿 自适应（金额展示用） */
export function fmtAmount(value: unknown): string {
  const n = toNum(value)
  if (n === null) return '--'
  const abs = Math.abs(n)
  if (abs >= 100000000) return `${(n / 100000000).toFixed(2)}亿`
  if (abs >= 10000) return `${(n / 10000).toFixed(2)}万`
  return n.toFixed(2)
}

/** 耗时：elapsed_ms → "1.23s" / "456ms" */
export function fmtMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`
  return `${Math.round(ms)}ms`
}

/** ISO 时间戳 → "YYYY-MM-DD HH:MM"（会话列表用） */
export function fmtDateTime(iso?: string): string {
  if (!iso) return '--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--'
  const pad = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 千分位金额（¥ 前缀由调用方给） */
export function fmtMoney(value: unknown, digits = 2): string {
  const n = toNum(value)
  if (n === null) return '--'
  return n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}