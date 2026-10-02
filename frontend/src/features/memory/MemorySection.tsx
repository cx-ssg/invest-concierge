import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BrainCircuit, Check, Database, Plus, Search, Sparkles, Trash2, X } from 'lucide-react'
import { api } from '../../lib/api'
import type { MemoryItem, MemoryKind, MemoryPendingItem } from '../../types/api'
import { Btn, Card, Empty, Hairline, Kicker, Spinner } from '../../components/ui/primitives'
import { useSessionStore } from '../../stores/session'

/**
 * 长期记忆区块（M2 · 设置页「隐私」之后）。
 *
 * 依据 `docs/COVERAGE_DESIGN.md` §4.2「记忆必须可审计、可删除」：
 * 总开关 / 列表 / 待确认候选 / 手动新增 / 召回预览 全部在这里给用户入口。
 * 设计原则：**AI 不自行写记忆** —— 隐式抽取先落候选，由用户逐条确认。
 * 所有请求都走 `api.memory.*`（组件内不裸写 fetch）。
 */

/** 三类记忆的中文名（顺序 = 后端 `VALID_KINDS`，`list_grouped` 恒返回三类键） */
const KIND_LABEL: Record<MemoryKind, string> = {
  preference: '偏好',
  fact: '事实',
  experience: '经验',
}

const KIND_ORDER: MemoryKind[] = ['preference', 'fact', 'experience']

/** 每类的召回语义说明（与 utils/long_memory 的注入策略一一对应） */
const KIND_HINT: Record<MemoryKind, string> = {
  preference: '风险承受度 / 风格 / 禁忌 —— 每次对话必注入',
  fact: '持仓 / 成本 / 长期计划 —— 按提问涉及的标的召回',
  experience: '历史决策 + 事后结果 —— 向量召回 top-3',
}

const SOURCE_LABEL: Record<string, string> = {
  explicit: '手动/显式',
  implicit: 'AI 抽取（已确认）',
  seed: '内置种子',
}

/** 后端时间是本地库的 "YYYY-MM-DD HH:MM:SS"，原样展示 */
function fmtTime(t?: string | null): string {
  return t ? String(t) : '--'
}

/** 事实类记忆常带 meta.code（如 600519），有则显式标出，便于核对召回范围 */
function metaCode(meta: Record<string, unknown> | undefined): string {
  const c = meta ? meta.code : null
  return c === null || c === undefined || c === '' ? '' : String(c)
}

/** 统一错误态（与设置页其它区块同风格） */
function ErrorLine({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <div className="mt-2 rounded-tile border border-hairline-strong bg-bg px-3 py-2 text-[12px] text-ink-2">
      ❌ 请求失败：{String(error).slice(0, 120)}
    </div>
  )
}

/** 单条记忆：[正文 + 来源/时间/键] + 删除 */
function MemoryRow({
  item,
  deleting,
  onDelete,
}: {
  item: MemoryItem
  deleting: boolean
  onDelete: () => void
}) {
  const code = metaCode(item.meta)
  return (
    <div className="mt-1.5 flex items-start gap-2 rounded-tile border border-hairline bg-bg px-2.5 py-1.5">
      <div className="min-w-0 flex-1">
        <div className="text-[12.5px] leading-relaxed break-words text-ink">{item.content}</div>
        <div className="mono mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-ink-3">
          <span>来源：{SOURCE_LABEL[item.source] ?? item.source}</span>
          {item.key ? <span>键：{item.key}</span> : null}
          {code ? <span>标的：{code}</span> : null}
          {item.session_id ? <span>会话 #{item.session_id}</span> : null}
          <span>更新：{fmtTime(item.updated_at || item.created_at)}</span>
        </div>
      </div>
      <Btn
        variant="danger"
        onClick={onDelete}
        disabled={deleting}
        title="删除这条记忆（删除后 AI 立刻看不到）"
      >
        {deleting ? <Spinner size={12} /> : <Trash2 size={12} />} 删除
      </Btn>
    </div>
  )
}

/** 单条待确认候选：正文 + [接受] / [拒绝] */
function PendingRow({
  item,
  busy,
  pendingAction,
  onResolve,
}: {
  item: MemoryPendingItem
  busy: boolean
  pendingAction?: 'accept' | 'reject'
  onResolve: (action: 'accept' | 'reject') => void
}) {
  const label = KIND_LABEL[item.kind as MemoryKind] ?? item.kind
  const code = metaCode(item.meta)
  return (
    <div className="mt-1.5 flex items-start gap-2 rounded-tile border border-hairline bg-bg px-2.5 py-1.5">
      <div className="min-w-0 flex-1">
        <div className="text-[12.5px] leading-relaxed break-words text-ink">{item.content}</div>
        <div className="mono mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-ink-3">
          <span>类别：{label}</span>
          {item.key ? <span>键：{item.key}</span> : null}
          {code ? <span>标的：{code}</span> : null}
          {item.session_id ? <span>会话 #{item.session_id}</span> : null}
          <span>抽取于：{fmtTime(item.created_at)}</span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <Btn variant="primary" onClick={() => onResolve('accept')} disabled={busy}>
          {busy && pendingAction === 'accept' ? <Spinner size={12} /> : <Check size={12} />} 接受
        </Btn>
        <Btn onClick={() => onResolve('reject')} disabled={busy} title="拒绝后这条候选不会再出现">
          {busy && pendingAction === 'reject' ? <Spinner size={12} /> : <X size={12} />} 拒绝
        </Btn>
      </div>
    </div>
  )
}

export function MemorySection() {
  const qc = useQueryClient()
  const activeSessionId = useSessionStore((s) => s.activeId)

  // ==================== 1. 总开关（照抄「隐私」区块的乐观切换 + 服务端校正） ====================
  const [switchLocal, setSwitchLocal] = useState<boolean | null>(null)
  const setQ = useQuery({ queryKey: ['memory', 'settings'], queryFn: api.memory.getSettings })
  const enabled = switchLocal ?? setQ.data?.enabled ?? true

  const invalidateAll = () => void qc.invalidateQueries({ queryKey: ['memory'] })

  const toggleMut = useMutation({
    mutationFn: (next: boolean) => api.memory.setSettings(next),
    onSuccess: (r) => {
      setSwitchLocal(r.enabled ?? false)
      invalidateAll()
    },
    onError: () => setSwitchLocal(null), // 失败回落到服务端值
  })

  // ==================== 2. 记忆列表（三类分组 + 删除） ====================
  const listQ = useQuery({ queryKey: ['memory', 'list'], queryFn: api.memory.list })
  const removeMut = useMutation({
    mutationFn: (id: number) => api.memory.remove(id),
    onSuccess: invalidateAll,
  })

  // ==================== 3. 待确认候选（接受 / 拒绝 / 按会话抽取） ====================
  const pendQ = useQuery({ queryKey: ['memory', 'pending'], queryFn: api.memory.pending })
  const [sumMsg, setSumMsg] = useState<string | null>(null)
  const resolveMut = useMutation({
    mutationFn: (p: { id: number; action: 'accept' | 'reject' }) =>
      api.memory.resolvePending(p.id, p.action),
    onSuccess: invalidateAll,
  })
  const summarizeMut = useMutation({
    mutationFn: () => api.memory.summarize(activeSessionId),
    onSuccess: (r) => {
      setSumMsg(
        r.ok
          ? r.added
            ? `已抽取 ${r.added} 条候选，请在下方确认`
            : '这段会话没有抽到值得长期记住的内容'
          : r.error || '抽取失败',
      )
      invalidateAll()
    },
    onError: (e) => setSumMsg(String(e).slice(0, 120)),
  })

  // ==================== 4. 手动新增 ====================
  const [newKind, setNewKind] = useState<MemoryKind>('preference')
  const [newContent, setNewContent] = useState('')
  const [addMsg, setAddMsg] = useState<string | null>(null)
  const addMut = useMutation({
    mutationFn: () => api.memory.add({ kind: newKind, content: newContent.trim() }),
    onSuccess: (r) => {
      if (r.ok) {
        setNewContent('')
        setAddMsg(`已记住（#${r.id ?? '--'}）`)
        invalidateAll()
      } else {
        setAddMsg(r.error ? `新增失败：${r.error}` : '新增失败')
      }
    },
    onError: (e) => setAddMsg(String(e).slice(0, 120)),
  })

  // ==================== 5. 召回预览（审计：AI 到底能看到什么） ====================
  const [question, setQuestion] = useState('')
  const [codes, setCodes] = useState('')
  const previewMut = useMutation({
    mutationFn: () => api.memory.recallPreview(question.trim(), codes.trim()),
  })

  const preview = previewMut.data
  const previewCounts = preview?.counts
  const previewEmpty =
    !!preview &&
    !preview.block &&
    !!previewCounts &&
    !previewCounts.preferences &&
    !previewCounts.facts &&
    !previewCounts.experiences

  const pendingItems = pendQ.data?.items ?? []

  return (
    <>
      {/* ========== 总开关 ========== */}
      <Card className="p-4">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <BrainCircuit size={14} className="text-ink-2" /> 长期记忆
        </div>
        <div className="mt-2 flex items-center gap-3">
          <button
            type="button"
            onClick={() => {
              const next = !enabled
              setSwitchLocal(next)
              void toggleMut.mutate(next)
            }}
            className={`relative h-5 w-9 cursor-pointer rounded-full border transition-colors ${
              enabled ? 'border-hairline-strong' : 'border-hairline'
            }`}
            style={{ background: enabled ? 'var(--accent-soft)' : 'var(--surface-2)' }}
            aria-pressed={enabled}
            aria-label="允许 AI 使用长期记忆"
          >
            <span
              className="absolute top-0.5 size-3.5 rounded-full transition-all"
              style={{
                left: enabled ? 19 : 3,
                background: enabled ? 'var(--accent)' : 'var(--text-3)',
                transitionDuration: 'var(--dur)',
              }}
            />
          </button>
          <span className="text-[12.5px] text-ink-2">
            允许 AI 使用长期记忆{enabled ? '（回答可结合下面这些记忆）' : '（已关闭）'}
          </span>
          {toggleMut.isPending || setQ.isFetching ? <Spinner size={12} /> : null}
        </div>
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-ink-3">
          开启后，对话时会把你确认过的长期记忆（偏好／相关事实／相关经验）随提问一并发送给模型用于个性化回答；
          关闭后 AI 不读取长期记忆，也不会暗示记得。记忆只存在本机数据库，删除即时生效。
        </p>
        <ErrorLine error={setQ.error} />
      </Card>

      {/* ========== 记忆列表（可审计 / 可删除） ========== */}
      <Card className="p-4">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <Database size={14} className="text-ink-2" /> 记忆列表
          <span className="text-[11.5px] text-ink-3">共 {listQ.data?.total ?? 0} 条</span>
          <div className="flex-1" />
          {listQ.isFetching ? <Spinner size={12} /> : null}
        </div>

        <ErrorLine error={listQ.error} />

        {listQ.isPending ? (
          <div className="mt-2 flex items-center gap-2 text-[12.5px] text-ink-2">
            <Spinner size={12} /> 加载记忆…
          </div>
        ) : null}

        {listQ.data && listQ.data.total === 0 ? (
          <Empty>暂无长期记忆 —— 可在下方手动新增，或接受 AI 抽取的候选。</Empty>
        ) : null}

        {listQ.data
          ? KIND_ORDER.map((k) => {
              const items = listQ.data.groups[k] ?? []
              return (
                <div key={k} className="mt-3">
                  <div className="flex flex-wrap items-center gap-2 px-1">
                    <Kicker>{KIND_LABEL[k]}</Kicker>
                    <span className="text-[11px] text-ink-3">
                      {items.length} 条 · {KIND_HINT[k]}
                    </span>
                  </div>
                  {items.length ? (
                    items.map((m) => (
                      <MemoryRow
                        key={m.id}
                        item={m}
                        deleting={removeMut.isPending && removeMut.variables === m.id}
                        onDelete={() => void removeMut.mutate(m.id)}
                      />
                    ))
                  ) : (
                    <div className="px-1 py-1 text-[11.5px] text-ink-3">（无）</div>
                  )}
                </div>
              )
            })
          : null}

        <ErrorLine error={removeMut.error} />
      </Card>

      {/* ========== 待确认候选 ========== */}
      <Card className="p-4">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <Sparkles size={14} className="text-ink-2" /> 待确认候选
          <span className="text-[11.5px] text-ink-3">{pendingItems.length} 条待处理</span>
          <div className="flex-1" />
          <Btn
            onClick={() => {
              setSumMsg(null)
              void summarizeMut.mutate()
            }}
            disabled={summarizeMut.isPending}
            title={
              activeSessionId
                ? '从当前选中的会话里抽取候选记忆'
                : '未选中会话：只会走「记住…」规则兜底'
            }
          >
            {summarizeMut.isPending ? <Spinner size={12} /> : <Sparkles size={12} />} 从当前会话抽取
          </Btn>
          {pendQ.isFetching ? <Spinner size={12} /> : null}
        </div>
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-ink-3">
          AI 不自行写记忆：隐式抽取的内容先落在这里，由你逐条确认。
          {activeSessionId ? `当前会话 #${activeSessionId}。` : '（当前未选中会话）'}
        </p>

        {pendQ.isPending ? (
          <div className="mt-2 flex items-center gap-2 text-[12.5px] text-ink-2">
            <Spinner size={12} /> 加载候选…
          </div>
        ) : null}
        {pendQ.data && !pendingItems.length ? <Empty>暂无待确认候选。</Empty> : null}

        {pendingItems.map((it) => (
          <PendingRow
            key={it.id}
            item={it}
            busy={resolveMut.isPending}
            pendingAction={resolveMut.isPending ? resolveMut.variables?.action : undefined}
            onResolve={(action) => void resolveMut.mutate({ id: it.id, action })}
          />
        ))}

        {sumMsg ? (
          <div className="mt-2 rounded-tile border border-hairline bg-bg px-3 py-2 text-[12px] text-ink-2">
            {sumMsg}
          </div>
        ) : null}
        <ErrorLine error={pendQ.error} />
        <ErrorLine error={resolveMut.error} />
      </Card>

      {/* ========== 手动新增 ========== */}
      <Card className="p-4">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <Plus size={14} className="text-ink-2" /> 手动新增记忆
        </div>
        <div className="mt-3 flex flex-col gap-2 md:flex-row md:items-end">
          <label className="block md:w-40">
            <Kicker>类别</Kicker>
            <select
              value={newKind}
              onChange={(e) => {
                setNewKind(e.target.value as MemoryKind)
                setAddMsg(null)
              }}
              className="mt-1 w-full rounded-tile border border-hairline bg-bg px-3 py-2 text-[13px] text-ink outline-none focus:border-hairline-strong"
            >
              {KIND_ORDER.map((k) => (
                <option key={k} value={k}>
                  {KIND_LABEL[k]}
                </option>
              ))}
            </select>
          </label>
          <label className="block min-w-0 flex-1">
            <Kicker>内容（一句话陈述，如「不碰杠杆」）</Kicker>
            <input
              value={newContent}
              onChange={(e) => {
                setNewContent(e.target.value)
                setAddMsg(null)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newContent.trim() && !addMut.isPending) {
                  void addMut.mutate()
                }
              }}
              placeholder={KIND_HINT[newKind]}
              className="mt-1 w-full rounded-tile border border-hairline bg-bg px-3 py-2 text-[13px] text-ink outline-none placeholder:text-ink-3 focus:border-hairline-strong"
            />
          </label>
          <Btn
            variant="primary"
            onClick={() => void addMut.mutate()}
            disabled={!newContent.trim() || addMut.isPending}
            className="justify-center px-3 py-2 md:w-24"
          >
            {addMut.isPending ? <Spinner size={12} /> : <Plus size={13} />} 记住
          </Btn>
        </div>
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-ink-3">
          这里新增的记忆与你在对话里说「记住…」等价（来源标记为「手动/显式」）。同类别同键会覆盖更新。
        </p>
        {addMsg ? (
          <div className="mt-2 rounded-tile border border-hairline bg-bg px-3 py-2 text-[12px] text-ink-2">
            {addMsg}
          </div>
        ) : null}
      </Card>

      {/* ========== 召回预览 ========== */}
      <Card className="p-4">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <Search size={14} className="text-ink-2" /> 召回预览
        </div>
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-ink-3">
          审计入口：模拟一次提问，看清「如果现在提问，AI 会看到哪些记忆」——所见即真实注入内容。
        </p>
        <div className="mt-3 flex flex-col gap-2 md:flex-row md:items-end">
          <label className="block min-w-0 flex-1">
            <Kicker>问题</Kicker>
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && question.trim() && !previewMut.isPending) {
                  void previewMut.mutate()
                }
              }}
              placeholder="如：600519 现在能加仓吗"
              className="mt-1 w-full rounded-tile border border-hairline bg-bg px-3 py-2 text-[13px] text-ink outline-none placeholder:text-ink-3 focus:border-hairline-strong"
            />
          </label>
          <label className="block md:w-48">
            <Kicker>涉及标的（可选，逗号分隔）</Kicker>
            <input
              value={codes}
              onChange={(e) => setCodes(e.target.value)}
              placeholder="600519,161725"
              className="mono mt-1 w-full rounded-tile border border-hairline bg-bg px-3 py-2 text-[12.5px] text-ink outline-none placeholder:text-ink-3 focus:border-hairline-strong"
            />
          </label>
          <Btn
            variant="primary"
            onClick={() => void previewMut.mutate()}
            disabled={!question.trim() || previewMut.isPending}
            className="justify-center px-3 py-2 md:w-24"
          >
            {previewMut.isPending ? <Spinner size={12} /> : <Search size={13} />} 预览
          </Btn>
        </div>

        {preview ? (
          <div className="mt-3">
            <Hairline />
            <div className="mono mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-3">
              <span>命中偏好 {previewCounts?.preferences ?? 0}</span>
              <span>事实 {previewCounts?.facts ?? 0}</span>
              <span>经验 {previewCounts?.experiences ?? 0}</span>
              <span>开关：{enabled ? '开' : '关'}</span>
            </div>
            {previewEmpty ? (
              <Empty>
                {enabled
                  ? '这个提问不会召回任何记忆（没有相关命中）—— AI 看到的长期记忆为空。'
                  : '长期记忆总开关已关闭 —— AI 不会看到任何记忆（上方计数恒为 0）。'}
              </Empty>
            ) : (
              <pre className="mono mt-2 max-h-64 overflow-auto rounded-tile border border-hairline bg-bg px-3 py-2 text-[11.5px] leading-relaxed whitespace-pre-wrap break-words text-ink-2">
                {preview.block}
              </pre>
            )}
          </div>
        ) : null}
        <ErrorLine error={previewMut.error} />
      </Card>
    </>
  )
}
