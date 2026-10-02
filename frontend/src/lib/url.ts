/**
 * 外链 scheme 白名单（A-R1 F5）—— 渲染层兜底。
 *
 * 来源卡的 `href` 取自**外部语料**（`utils/rag/sources.py` 从 kb.db 搬运）。
 * 独立审计实测：`javascript:` 之所以没执行，只是 React 19 渲染时把它替换成抛错串
 * —— 那是**框架行为，不是本仓保证**；`data:text/html,...` 则被原样输出。
 * 后端已在 `extract_sources` 做数据层收窄（非 http/https 置 None），此处再拦一道：
 * 只有 http/https 给可点外链，其余降级为纯文本（`SourceList` 不渲染 `<a>`）。
 */
export function isSafeExternalUrl(url: string | null | undefined): boolean {
  if (!url) return false
  try {
    const scheme = new URL(String(url).trim()).protocol.toLowerCase()
    return scheme === 'http:' || scheme === 'https:'
  } catch {
    // 相对路径（new URL 无 base 会抛）/ 畸形串 ⇒ 不是可点外链
    return false
  }
}
