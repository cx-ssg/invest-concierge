import type { ReactNode } from 'react'
import { Kicker } from '../../components/ui/primitives'

/** 内容区页头：面包屑式小标题 + 副说明（页面统一开场） */
export function Panel({
  title,
  desc,
  children,
}: {
  title: string
  desc?: string
  children: ReactNode
}) {
  return (
    <section className="flex min-w-0 flex-1 flex-col gap-4">
      <div className="flex flex-col gap-1">
        <Kicker>INVEST CONCIERGE</Kicker>
        <h1 className="px-1 text-lg font-semibold text-ink">{title}</h1>
        {desc ? <p className="px-1 text-[13px] text-ink-2">{desc}</p> : null}
      </div>
      {children}
    </section>
  )
}
