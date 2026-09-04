import type { ReactNode } from 'react'

/**
 * 页面标题区（UI_POLISH_PLAN §2.3 统一规格）：
 * 模块 eyebrow / 标题 22px·700 / 描述 13px / 右侧操作区 / 底部发丝线。
 * 开发信息（API 路径、存储细节）禁止出现在这里——收纳到设置页。
 */
export function PageHeader({
  eyebrow,
  title,
  desc,
  actions,
}: {
  eyebrow?: string
  title: string
  desc?: string
  actions?: ReactNode
}) {
  return (
    <div className="flex items-end justify-between gap-3 border-b border-hairline pb-4 pt-7">
      <div className="flex min-w-0 flex-col gap-1.5">
        {eyebrow ? (
          <div className="px-1 text-[11px] tracking-[1.5px] text-ink-3 select-none">{eyebrow}</div>
        ) : null}
        <h1 className="px-1 text-[22px] font-bold leading-tight text-ink">{title}</h1>
        {desc ? <p className="px-1 text-[13px] leading-5 text-ink-2">{desc}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2 pb-1">{actions}</div> : null}
    </div>
  )
}
