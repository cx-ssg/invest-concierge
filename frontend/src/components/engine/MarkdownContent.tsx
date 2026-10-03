import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { RetrievalSource } from '../../types/api'

/**
 * A2 引用回跳：正文上标 `[n]` → 滚到**本消息**的来源卡 `#src-{scope}-{n}` 并短暂高亮 1.5s。
 * 来源卡由 `ChatArea` 的 `SourceList` 渲染（同一个 `scope`）。
 * 找不到目标（来源卡尚未渲染）时静默返回 —— 不让点击炸掉页面。
 *
 * ⚠️ **H1（2026-10-03）id 必须按消息唯一**：旧实现是全局 `src-{n}` —— 历史多条消息
 * 同页时 `src-1` 重复，`getElementById` 只命中**文档里第一条**，于是「点第 2 条消息的 [1]」
 * 会滚到第 1 条消息的位置（错位）。现在 id = `src-{scope}-{n}`，`scope` 由调用方按消息给定
 * （`ChatArea`：历史消息 `h-{i}`、运行中 `live-{runId}`）。
 * 回归锁：`scripts/verify_a2_dom.mjs` 的 `H1/*` 场景（两条消息各 5 条来源 ⇒ 10 个 id 全唯一，
 * 且点击的滚动目标落在**本条消息**的卡片上）。
 */
export function sourceCardId(scope: string, n: number): string {
  return `src-${scope}-${n}`
}

export function jumpToSource(scope: string, n: number) {
  const el = document.getElementById(sourceCardId(scope, n))
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('src-flash')
  window.setTimeout(() => el.classList.remove('src-flash'), 1500)
}

/** `#src-{scope}-{n}` 反向解析（scope 自身可能含 `-`，故 n 取**最后一段数字**）。 */
const SRC_HREF = /^#src-(.+)-(\d+)$/

/**
 * 把正文里可引用的 `[n]` 换成 markdown 链接 `[[n]](#src-{scope}-{n})`，交给 `components.a` 渲染成上标按钮。
 *
 * ⚠️ **跳过 fenced code block（``` / ~~~ 之间，含未闭合的）、4 空格 / Tab 缩进代码块与行内 code**
 *    —— 否则代码里的 `[0]` / `arr[1]` 会被误改成链接（真实事故形态：贴一段 Python 示例就把正文改坏）。
 *    ⚠️ 2026-10-03 A-R1 F3：原正则只有「闭合的 ``` / ~~~ + 行内 code」，
 *    未闭合 `~~~` 与 4 空格缩进块**仍被替换**（审计用真组件 SSR 复现：
 *    输入 `'~~~\narr[1] = 2\n'` → `<pre><code>arr[[1]](#src-1) = 2`），与注释声明不符。
 *    现补 `~~~[\s\S]*$`（未闭合到文末）与 `^(?: {4}|\t)[^\n]*$`（缩进码行，需 `m` 标志）两条分支。
 * ⚠️ **越界编号保持普通文本**（如只有 3 条来源却写了 `[9]`）—— 不造点不动的死链。
 * ⚠️ 不碰 `[1](url)` 这种本就是 markdown 链接的方括号（负向先行断言 `(?!\()`）。
 * ⚠️ 缩进分支会把「4 空格缩进的段落续行」也一并保护（CommonMark 里那种续行不是代码块）——
 *    这是**保守方向**的取舍：宁可少链接一处引用，也不把用户贴的代码改坏。
 */
function linkifyCitations(content: string, max: number, scope: string): string {
  if (!content || max <= 0) return content
  const RE = /```[\s\S]*?```|~~~[\s\S]*?~~~|```[\s\S]*$|~~~[\s\S]*$|^(?: {4}|\t)[^\n]*$|`[^`\n]*`|\[(\d+)\](?!\()/gm
  return content.replace(RE, (match: string, num?: string) => {
    if (num === undefined) return match // 代码块 / 缩进码行 / 行内 code：原样保留
    const n = Number(num)
    if (!Number.isInteger(n) || n < 1 || n > max) return match
    return `[[${n}]](#${sourceCardId(scope, n)})`
  })
}

/** `a` 渲染器：本消息的 `#src-{scope}-{n}` → 上标按钮；其余外链统一新窗口打开（原行为保持）。 */
function mdComponents(scope: string): Components {
  return {
    a: ({ href, children }) => {
      const m = href ? SRC_HREF.exec(href) : null
      // 只认**本条消息**的 scope：别的消息的引用上标不该在这里变成可点按钮
      if (m && m[1] === scope) {
        const n = Number(m[2])
        return (
          <sup>
            <button
              type="button"
              onClick={() => jumpToSource(scope, n)}
              title={`跳到来源 [${n}]`}
              className="mono cursor-pointer px-0.5 text-[10.5px] text-accent underline-offset-2 hover:underline"
            >
              {children}
            </button>
          </sup>
        )
      }
      return (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      )
    },
  }
}

/**
 * Agent 回复的 Markdown 渲染（GFM：表格/列表/引用/代码）。
 * 历史回放与完成态使用——保证了表格等结构的完整渲染。
 *
 * A2：可选 `sources` —— 传了才把正文 `[n]` 变成可点上标；
 * **不传/为空时行为与改造前逐字一致**（历史回放、诊断页、周报都走这条路，零回归）。
 *
 * H1：有 `sources` 时 **`scope` 必填**（类型层面强制）—— 它是本渲染实例的引用编号
 * 命名空间（来源卡 id = `src-{scope}-{n}`），由调用方按消息给唯一值 ⇒ 同页多条消息的
 * 来源卡 id 不再互撞。无 `sources` 的调用方（诊断页/周报）不必传，行为零变化。
 */
type MarkdownContentProps =
  | { content: string; sources: RetrievalSource[]; scope: string }
  | { content: string; sources?: undefined; scope?: string }

export function MarkdownContent({ content, sources, scope }: MarkdownContentProps) {
  if (!content) return <span className="text-ink-3">（此条为空回复）</span>
  const max = sources?.length ?? 0
  // scope 缺失（无 sources 的调用方）⇒ 不链接化，绝不退回全局 `src-{n}`（那正是 H1 缺陷）
  const scopeId = scope ?? ''
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={max > 0 && scopeId ? mdComponents(scopeId) : undefined}
      >
        {linkifyCitations(content, max, scopeId)}
      </ReactMarkdown>
    </div>
  )
}
