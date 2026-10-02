import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { RetrievalSource } from '../../types/api'

/**
 * A2 引用回跳：正文上标 `[n]` → 滚到来源卡 `#src-{n}` 并短暂高亮 1.5s。
 * 来源卡由 `ChatArea` 的 `SourceList` 渲染（`id="src-{n}"`）。
 * 找不到目标（来源卡尚未渲染）时静默返回 —— 不让点击炸掉页面。
 */
export function jumpToSource(n: number) {
  const el = document.getElementById(`src-${n}`)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('src-flash')
  window.setTimeout(() => el.classList.remove('src-flash'), 1500)
}

/**
 * 把正文里可引用的 `[n]` 换成 markdown 链接 `[[n]](#src-n)`，交给 `components.a` 渲染成上标按钮。
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
function linkifyCitations(content: string, max: number): string {
  if (!content || max <= 0) return content
  const RE = /```[\s\S]*?```|~~~[\s\S]*?~~~|```[\s\S]*$|~~~[\s\S]*$|^(?: {4}|\t)[^\n]*$|`[^`\n]*`|\[(\d+)\](?!\()/gm
  return content.replace(RE, (match: string, num?: string) => {
    if (num === undefined) return match // 代码块 / 缩进码行 / 行内 code：原样保留
    const n = Number(num)
    if (!Number.isInteger(n) || n < 1 || n > max) return match
    return `[[${n}]](#src-${n})`
  })
}

/** `a` 渲染器：`#src-n` → 上标按钮；其余外链统一新窗口打开（原行为保持）。 */
const MD_COMPONENTS: Components = {
  a: ({ href, children }) => {
    if (href && href.startsWith('#src-')) {
      const n = Number(href.slice('#src-'.length))
      return (
        <sup>
          <button
            type="button"
            onClick={() => jumpToSource(n)}
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

/**
 * Agent 回复的 Markdown 渲染（GFM：表格/列表/引用/代码）。
 * 历史回放与完成态使用——保证了表格等结构的完整渲染。
 *
 * A2：可选 `sources` —— 传了才把正文 `[n]` 变成可点上标；
 * **不传/为空时行为与改造前逐字一致**（历史回放、诊断页、周报都走这条路，零回归）。
 */
export function MarkdownContent({
  content,
  sources,
}: {
  content: string
  /** 本次回答可引用的来源（数组下标 +1 = 正文引用编号 [n]） */
  sources?: RetrievalSource[]
}) {
  if (!content) return <span className="text-ink-3">（此条为空回复）</span>
  const max = sources?.length ?? 0
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={max > 0 ? MD_COMPONENTS : undefined}
      >
        {linkifyCitations(content, max)}
      </ReactMarkdown>
    </div>
  )
}
