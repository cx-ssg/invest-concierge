import type { CSSProperties, ReactNode } from 'react'

/** 状态点：6px 圆点——ready 常亮 / thinking 呼吸 / off 灰暗 */
export function StatusDot({ state }: { state: 'ready' | 'thinking' | 'off' }) {
  const style: CSSProperties =
    state === 'ready'
      ? { background: 'var(--accent)' }
      : state === 'off'
        ? { background: 'var(--text-3)', opacity: 0.5 }
        : { background: 'var(--accent)', animation: 'engine-pulse 1.6s var(--ease) infinite' }
  return <span className="inline-block size-[6px] shrink-0 rounded-full" style={style} />
}

/** 分组小标题：11px 字距 1.5px 灰（对应旧 .panel-label） */
export function Kicker({ children }: { children: ReactNode }) {
  return (
    <div className="px-1 text-[11px] tracking-[1.5px] text-ink-3 select-none">{children}</div>
  )
}

/** 水平发丝线 */
export function Hairline() {
  return <div className="border-t border-hairline" />
}

/** 卡片：surface 底 + 发丝线描边 + 8px 圆角（无阴影无渐变） */
export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-card border border-hairline bg-surface ${className}`}>{children}</div>
}

/** 徽章：小标签（状态/版本/引擎名） */
export function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: 'neutral' | 'rise' | 'fall'
}) {
  const toneCls = tone === 'rise' ? 'text-rise' : tone === 'fall' ? 'text-fall' : 'text-ink-2'
  return (
    <span
      className={`mono inline-flex items-center rounded-tile border border-hairline bg-surface-2 px-1.5 py-px text-[11px] ${toneCls}`}
    >
      {children}
    </span>
  )
}

/** 等宽数字（金额/涨跌/耗时）：涨红跌绿由调用方给 tone */
export function Num({
  value,
  tone,
}: {
  value: string | number
  tone?: 'rise' | 'fall' | 'flat'
}) {
  const toneCls = tone === 'rise' ? 'text-rise' : tone === 'fall' ? 'text-fall' : 'text-ink-2'
  return <span className={`num ${toneCls}`}>{value}</span>
}

/** 按钮：主/次/危险三态（克制动效：hover 变色 + 160ms 过渡） */
export function Btn({
  children,
  onClick,
  variant = 'ghost',
  type = 'button',
  disabled = false,
  className = '',
  title,
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'primary' | 'ghost' | 'danger'
  type?: 'button' | 'submit'
  disabled?: boolean
  className?: string
  title?: string
}) {
  const base =
    'flex cursor-pointer items-center gap-1.5 rounded-tile px-2.5 py-1 text-[12px] transition-colors select-none disabled:cursor-not-allowed disabled:opacity-40'
  const variantCls =
    variant === 'primary'
      ? 'border border-hairline-strong bg-surface-2 font-medium text-ink hover:bg-surface'
      : variant === 'danger'
        ? 'border border-hairline text-fall hover:bg-surface'
        : 'border border-hairline text-ink-2 hover:bg-surface hover:text-ink'
  return (
    <button
      type={type}
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`${base} ${variantCls} ${className}`}
      style={{ transitionDuration: 'var(--dur)' }}
    >
      {children}
    </button>
  )
}

/** 页签条：active 下划线式（Kicker 风格小字） */
export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: string; label: string }[]
  active: string
  onChange: (key: string) => void
}) {
  return (
    <div className="hairline-b flex gap-1 overflow-x-auto">
      {tabs.map((t) => {
        const isActive = t.key === active
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => onChange(t.key)}
            className={`cursor-pointer whitespace-nowrap px-2 py-1.5 text-[12px] transition-colors ${
              isActive
                ? 'border-b-2 border-accent font-medium text-ink'
                : 'border-b-2 border-transparent text-ink-3 hover:text-ink'
            }`}
            style={{ transitionDuration: 'var(--dur)' }}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}

/** 表单字段：小标签 + 输入控件 */
export function Field({
  label,
  children,
  className = '',
}: {
  label: string
  children: ReactNode
  className?: string
}) {
  return (
    <label className={`flex flex-col gap-1 ${className}`}>
      <span className="px-0.5 text-[11px] text-ink-3">{label}</span>
      {children}
    </label>
  )
}

/** 输入框统一样式（数字/文本共用） */
export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-tile border border-hairline bg-bg px-2 py-1 text-[13px] text-ink outline-none placeholder:text-ink-3 focus:border-hairline-strong ${
        props.className ?? ''
      }`}
    />
  )
}

/** 空态文案 */
export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-2 py-4 text-[13px] text-ink-3">{children}</div>
}

/** 加载圈：小（16ms 间歇旋转，克制） */
export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <span
      aria-label="加载中"
      className="inline-block shrink-0 rounded-full border border-hairline-strong border-t-accent"
      style={{ width: size, height: size, animation: 'engine-spin 0.9s linear infinite' }}
    />
  )
}

/** 评级色横幅卡（对齐 _render_badge：左侧 3px 语义色竖条 + 大字值） */
export function RatingBanner({
  label,
  value,
  color,
  sub,
}: {
  label: string
  value: string
  color?: string
  sub?: string
}) {
  return (
    <div
      className="rounded-card border border-hairline bg-surface px-4 py-3"
      style={{ borderLeft: `3px solid ${color ?? 'var(--text-3)'}` }}
    >
      <div className="text-[11px] text-ink-3">{label}</div>
      <div className="mt-0.5 text-[19px] font-semibold" style={{ color: color ?? 'var(--text-3)' }}>
        {value}
      </div>
      {sub ? <div className="mt-0.5 text-[12px] text-ink-2">{sub}</div> : null}
    </div>
  )
}