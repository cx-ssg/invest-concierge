import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Check, Inbox, Pin } from 'lucide-react'
import { api } from '../../lib/api'
import type { SessionSummary } from '../../types/api'
import { ContextMenu } from '../../components/ui/ContextMenu'
import { useSessionStore } from '../../stores/session'

/**
 * 历史会话列表（M2 布局改版：收进主侧栏「对话」下方，替代 AiChatPage 内独立 230px 侧栏）。
 * 数据 = /api/agent/sessions + PATCH/DELETE 写后失效；右键菜单（重命名/置顶/归档/删除）保留。
 * 选中态走 useSessionStore（与聊天区共用）。
 */
export function SessionList({ onOpenChat }: { onOpenChat: (id: number | null) => void }) {
  const qc = useQueryClient()
  const activeId = useSessionStore((s) => s.activeId)
  const setActiveId = useSessionStore((s) => s.setActiveId)
  const [view, setView] = useState<'active' | 'archived'>('active')
  const [ctx, setCtx] = useState<{ x: number; y: number; id: number } | null>(null)
  const [renaming, setRenaming] = useState<number | null>(null)
  const [renameText, setRenameText] = useState('')

  const { data: sessions } = useQuery({
    queryKey: ['agent-sessions', view],
    queryFn: () => api.agent.sessions(view === 'archived'),
    staleTime: 8_000,
  })

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['agent-sessions'] })
  }

  const patchMut = useMutation({
    mutationFn: (p: { id: number; action: string; title?: string }) =>
      api.agent.patchSession(p.id, p.action, p.title ?? ''),
    onSuccess: invalidate,
  })

  const delMut = useMutation({
    mutationFn: (id: number) => api.agent.deleteSession(id),
    onSuccess: () => {
      invalidate()
      if (activeId === ctx?.id) onOpenChat(null)
    },
  })

  const list = (sessions ?? []).slice(0, view === 'active' ? 12 : 20)

  function openMenu(e: React.MouseEvent, s: SessionSummary) {
    e.preventDefault()
    setRenaming(null)
    setCtx({ x: e.clientX, y: e.clientY, id: s.id })
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
        return [
          {
            key: 'rename',
            label: '重命名',
            onClick: () => {
              setRenaming(s.id)
              setRenameText(s.title)
            },
          },
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
      })()
    : []

  const itemCls = (active: boolean) =>
    `group flex cursor-pointer items-center gap-1.5 rounded-tile px-2 py-1.5 transition-colors ${
      active ? 'bg-surface-2' : 'hover:bg-surface'
    }`

  return (
    <div className="flex min-h-0 flex-col">
      {/* 视图切换：会话 / 回收站 */}
      <div className="flex items-center gap-1 px-1 pb-1">
        <span className="flex-1 text-[11px] tracking-[1.5px] text-ink-3 select-none">
          {view === 'active' ? '历史会话' : '回收站'}
        </span>
        <button
          type="button"
          title={view === 'active' ? '查看已归档会话' : '返回会话列表'}
          onClick={() => setView((v) => (v === 'active' ? 'archived' : 'active'))}
          className="cursor-pointer text-ink-3 hover:text-ink"
        >
          {view === 'active' ? <Archive size={12} /> : <Inbox size={12} />}
        </button>
      </div>

      <div className="min-h-0 max-h-[240px] overflow-y-auto">
        <div className="flex flex-col gap-0.5">
          {list.map((s) => {
            const active = s.id === activeId
            const title = s.title || '未命名会话'
            return (
              <div
                key={s.id}
                role="button"
                tabIndex={0}
                onClick={() => {
                  setActiveId(s.id)
                  onOpenChat(s.id)
                }}
                onContextMenu={(e) => openMenu(e, s)}
                className={itemCls(active)}
              >
                {renaming === s.id ? (
                  <div className="flex w-full items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <input
                      autoFocus
                      value={renameText}
                      onChange={(e) => setRenameText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitRename()
                        if (e.key === 'Escape') setRenaming(null)
                      }}
                      className="w-full rounded-tile border border-hairline-strong bg-surface px-1.5 py-0.5 text-[12px] text-ink outline-none"
                    />
                    <button
                      type="button"
                      aria-label="确认重命名"
                      onClick={commitRename}
                      className="cursor-pointer text-ink-2 hover:text-ink"
                    >
                      <Check size={12} />
                    </button>
                  </div>
                ) : (
                  <>
                    {s.pinned ? <Pin size={11} className="shrink-0 text-ink-3" /> : null}
                    <span
                      className={`min-w-0 flex-1 truncate text-[12px] ${active ? 'text-ink' : 'text-ink-2'}`}
                      title={title}
                    >
                      {title}
                    </span>
                  </>
                )}
              </div>
            )
          })}
          {!list.length ? (
            <div className="px-2 py-2 text-[11.5px] text-ink-3">
              {view === 'active' ? '暂无会话' : '回收站为空'}
            </div>
          ) : null}
        </div>
      </div>

      {ctx ? <ContextMenu x={ctx.x} y={ctx.y} items={menuItems} onClose={() => setCtx(null)} /> : null}
    </div>
  )
}
