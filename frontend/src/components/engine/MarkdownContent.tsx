import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * Agent 回复的 Markdown 渲染（GFM：表格/列表/引用/代码）。
 * 历史回放与完成态使用——保证了表格等结构的完整渲染。
 */
export function MarkdownContent({ content }: { content: string }) {
  if (!content) return <span className="text-ink-3">（此条为空回复）</span>
  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  )
}