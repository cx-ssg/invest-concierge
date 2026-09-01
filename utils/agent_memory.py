# -*- coding: utf-8 -*-
"""
Agent 记忆层：跨页会话持久化（"越用越懂"的关键）。

- 复用 data/database.py 的 SQLite 模式（agent_sessions / agent_messages 表在 init_db 建表）。
- 会话满 SUMMARY_TRIGGER_ROUNDS（8）轮生成一句话摘要，防 prompt token 爆。
- 诊断追问注入最近 MEMORY_CONTEXT_SESSIONS（3）条会话摘要（按 session 更新倒序，规则定死可测）。
- 无 key 降级：记忆照常落库，摘要由"最近消息末尾截取"兜底，不调用 LLM。

实现依据：docs/AGENT_MVP_DESIGN.md §2.③ / §7 验收（注入规则：最近 3 条、按更新倒序）。
"""
import sys

from config import API_KEY
from data.database import (
    create_agent_session,
    add_agent_message,
    get_agent_messages,
    count_agent_messages,
    update_agent_session_summary,
    list_recent_agent_sessions,
)

# 每会话满 N 轮（用户提问数）后生成摘要
SUMMARY_TRIGGER_ROUNDS = 8
# 诊断追问注入的最近会话摘要条数（按更新倒序取最近 3 条）
MEMORY_CONTEXT_SESSIONS = 3
# 无 key / LLM 失败时，摘要降级为"最近消息末尾截取"的长度
FALLBACK_SUMMARY_LEN = 80

# Windows 控制台 GBK 防护
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def summarize_session(session_id, llm_fn=None):
    """生成会话一句话摘要并落库。

    有 key：调 LLM 压缩成一句话；无 key / LLM 失败：最近消息末尾截取兜底。
    返回最终摘要字符串（无消息时返回 "" 且不落库）。
    """
    messages = get_agent_messages(session_id)
    if not messages:
        return ""

    if API_KEY:
        try:
            if llm_fn is None:
                # 晚绑定：避免 agent_memory → ai_helper → agent_core → agent_memory 循环 import，
                # 且让测试可以 patch utils.ai_helper.call_llm。
                from utils import ai_helper
                llm_fn = ai_helper.call_llm
            transcript = "\n".join(
                "- {}: {}".format(m.get("role"), str(m.get("content") or "")[:500])
                for m in messages[-60:]
            )
            result = llm_fn([
                {"role": "system", "content": "用一句话总结这段投资咨询对话的核心主题、用户的关注点或持仓偏好（40 字以内）："},
                {"role": "user", "content": transcript[:2500]},
            ])
            if result and result.get("type") == "text":
                summary = (result.get("content") or "").strip()
                if summary:
                    update_agent_session_summary(session_id, summary)
                    return summary
        except Exception as e:  # noqa: BLE001 - LLM 失败降级，不阻断
            print("会话摘要生成失败（{}）：{}".format(session_id, e))

    # 无 key / LLM 失败：最近消息末尾截取
    last = str(messages[-1].get("content") or "").strip()
    summary = last if len(last) <= FALLBACK_SUMMARY_LEN else last[:FALLBACK_SUMMARY_LEN] + "…"
    update_agent_session_summary(session_id, summary)
    return summary


def maybe_summarize_session(session_id):
    """会话满 8 轮（用户消息数为 8 的倍数）时触发摘要；返回当前用户消息数（可测）。"""
    n = count_agent_messages(session_id, role="user")
    if n > 0 and n % SUMMARY_TRIGGER_ROUNDS == 0:
        summarize_session(session_id)
    return n


def build_memory_context(limit=MEMORY_CONTEXT_SESSIONS):
    """构建注入 prompt 的会话记忆上下文：取最近 limit 条已有摘要的会话，按更新倒序。

    规则定死（验收标准可测）：最近 3 条、按 session 更新倒序；无摘要返回空串。
    """
    sessions = list_recent_agent_sessions(limit)
    if not sessions:
        return ""
    lines = []
    for s in sessions:
        title = s.get("title") or ""
        summary = s.get("summary") or ""
        lines.append("- 【{}】会话#{}：{}".format(title, s.get("id"), summary))
    return "最近会话记忆：\n" + "\n".join(lines)


def ensure_session(session_id, title=""):
    """确保存在会话：None/0/非法值 → 新建；否则原样返回（页面追问复用同一会话）。

    返回值一定是数据库里的整数会话 id，调用方应保存返回值而非自行构造
    （session_id 列是 INTEGER，字符串键会静默写入失败）。
    """
    if isinstance(session_id, int) and session_id > 0:
        return session_id
    return create_agent_session(title=title)


def record_message(session_id, role, content):
    """落库一条消息（对外薄封装，页面可复用）"""
    return add_agent_message(session_id, role, content)