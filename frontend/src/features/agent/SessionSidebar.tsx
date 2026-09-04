import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Check, Edit3, Inbox, MessageSquarePlus, Pin, Sparkles } from 'lucide-react'
import { api } from '../../lib/api'
import type { SessionSummary } from '../../types/api'
import { fmtDateTime } from '../../lib/format'
import { Btn, Kicker, Spinner, TextInput } from '../../components/ui/primitives'
import { ContextMenu } from '../../components/ui/ContextMenu'

export interface SessionSidebarProps {
  activeId: number | null
  onSelect: (id: number | null) => void
}

/**
 * 会话侧栏（Reasonix 式「会话在左」）：列表 / 新建 / 右键菜单
 * （重命名 / 置顶 / 归档 / 删除）+ 已归档回收站视图。
 * 数据 = /api/agent/sessions + PATCH / DELETE 写后失效。
 */
export function SessionSidebar({ activeId, onSelect }: SessionSidebarProps) {
  const qc = useQueryClient()
  const [view, setView] = useState<'active' | 'archived'>('active')
  const [ctx, setCtx] = useState<{ x: number; y: number; id: number } | null>(null)
  const [renaming, setRenaming] = useState<number | null>(null)
  const [renameText, setRenameText] = useState('')

  const { data: sessions, isFetching } = useQuery({
    queryKey: ['agent-sessions', view],
    queryFn: () => api.agent.sessions(view === 'archived'),
    staleTime: 8_000,
  })

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['agent-sessions'] })
    void qc.invalidateQueries({ queryKey: ['agent-sessions', 'active'] })
    void qc.invalidateQueries({ queryKey: ['agent-sessions', 'archived'] })
  }

  const createMut = useMutation({
    mutationFn: () => api.agent.createSession(''),
    onSuccess: (r) => {
      invalidate()
      if (r.session_id) {
        setView('active')
        onSelect(r.session_id)
      }
    },
  })

  const patchMut = useMutation({
    mutationFn: (p: { id: number; action: string; title?: string }) =>
      api.agent.patchSession(p.id, p.action, p.title ?? ''),
    onSuccess: invalidate,
  })

  const delMut = useMutation({
    mutationFn: (id: number) => api.agent.deleteSession(id),
    onSuccess: () => {
      invalidate()
      if (activeId && activeId === ctx?.id) onSelect(null)
    },
  })

  const list = sessions ?? []

  function openMenu(e: React.MouseEvent, s: SessionSummary) {
    e.preventDefault()
    setRenaming(null)
    setCtx({ x: e.clientX, y: e.clientY, id: s.id })
  }

  function startRename(s: SessionSummary) {
    setRenaming(s.id)
    setRenameText(s.title)
  }

  function commitRename() {
    if (renaming != null) {
      const t = renameText.trim()
      if (t) void patchMut.mutateAsync({ id: renaming, action: 'rename', title: t })
    }
    setRenaming(null)
  }

  const menuItems = ctx
    ? (() => {
        const s = list.find((x) => x.id === ctx.id)
        if (!s) return []
        const base = [
          { key: 'rename', label: '重命名', onClick: () => startRename(s) },
          {
            key: 'pin',
            label: s.pinned ? '取消置顶' : '置顶',
            onClick: () => void patchMut.mutate({ id: s.id, action: s.pinned ? 'unpin' : 'pin' }),
          },
          {
            key: 'archive',
            label: view === 'archived' ? '恢复' : '归档',
            onClick: () =>
              void patchMut.mutate({ id: s.id, action: view === 'archived' ? 'restore' : 'archive' }),
          },
          {
            key: 'delete',
            label: '删除',
            danger: true,
            onClick: () => {
              if (window.confirm(`确认删除会话「${s.title || `#${s.id}`}」？消息将一并删除。`)) {
                void delMut.mutate(s.id)
              }
            },
          },
        ]
        return base
      })()
    : []

  return (
    <aside className="flex h-full min-h-0 w-[230px] shrink-0 flex-col border-r border-hairline">
      {/* 头部：新建 + 视图切换 */}
      <div className="flex items-center gap-1.5 p-2">
        <Btn
          variant="primary"
          className="flex-1 justify-center"
          onClick={() => void createMut.mutate()}
          disabled={createMut.isPending}
        >
          {createMut.isPending ? <Spinner size={11} /> : <MessageSquarePlus size={13} />}
          新建会话
        </Btn>
        <Btn
          title={view === 'active' ? '查看已归档会话' : '返回会话列表'}
          onClick={() => setView((v) => (v === 'active' ? 'archived' : 'active'))}
        >
          {view === 'active' ? <Archive size={13} /> : <Inbox size={13} />}
        </Btn>
      </div>

      {/* 会话列表 */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <Kicker>{view === 'active' ? '会话' : '已归档'}</Kicker>
        {isFetching && !list.length ? (
          <div className="flex items-center gap-2 px-2 py-3 text-[12px] text-ink-3">
            <Spinner size={11} /> 加载会话…
          </div>
        ) : null}
        <div className="mt-1 flex flex-col gap-0.5">
          {list.map((s) => {
            const active = s.id === activeId
            const title = s.title || '未命名会话'
            return (
              <div
                key={s.id}
                role="button"
                tabIndex={0}
                onClick={() => {
                  onSelect(s.id)
                }}
                onContextMenu={(e) => openMenu(e, s)}
                className={`group flex cursor-pointer flex-col gap-0.5 rounded-tile border border-transparent px-2 py-1.5 transition-colors ${
                  active ? 'border-hairline bg-surface-2' : 'hover:bg-surface'
                }`}
                style={{ transitionDuration: 'var(--dur)' }}
              >
                {renaming === s.id ? (
                  <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <TextInput
                      autoFocus
                      value={renameText}
                      onChange={(e) => setRenameText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitRename()
                        if (e.key === 'Escape') setRenaming(null)
                      }}
                      className="!py-0.5 text-[12px]"
                    />
                    <button
                      type="button"
                      aria-label="确认重命名"
                      onClick={commitRename}
                      className="cursor-pointer text-ink-2 hover:text-ink"
                    >
                      <Check size={13} />
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="flex items-center gap-1.5">
                      {s.pinned ? <Pin size={11} className="shrink-0 text-ink-3" /> : null}
                      <span className={`min-w-0 flex-1 truncate text-[12.5px] ${active ? 'text-ink' : 'text-ink-2'}`}>
                        {title}
                      </span>
                      <button
                        type="button"
                        aria-label="会话操作"
                        onClick={(e) => {
                          e.stopPropagation()
                          openMenu(e as unknown as React.MouseEvent, s)
                        }}
                        className="invisible cursor-pointer text-ink-3 hover:text-ink group-hover:visible"
                      >
                        <Edit3 size={12} />
                      </button>
                    </div>
                    <div className="flex items-center gap-1.5 text-[10.5px] text-ink-3">
                      {s.summary ? <span className="min-w-0 flex-1 truncate">{s.summary}</span> : null}
                      <span className="shrink-0 mono">{fmtDateTime(s.updated_at)}</span>
                    </div>
                  </>
                )}
              </div>
            )
          })}
          {!list.length && !isFetching ? (
            <div className="px-2 py-3 text-[12px] text-ink-3">
              {view === 'active' ? '暂无会话，点「新建会话」开始' : '回收站为空'}
            </div>
          ) : null}
        </div>
      </div>

      {/* 置顶说明 */}
      <div className="flex items-center gap-1.5 border-t border-hairline px-2 py-1.5 text-[10.5px] text-ink-3">
        <Sparkles size={11} />
        <span>右键会话可重命名 / 置顶 / 归档</span>
      </div>

      {ctx ? <ContextMenu x={ctx.x} y={ctx.y} items={menuItems} onClose={() => setCtx(null)} /> : null}
    </aside>
  )
}