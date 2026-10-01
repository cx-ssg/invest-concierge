# -*- coding: utf-8 -*-
"""M2 长期记忆层 —— 实现（Step 3）。

设计与取舍：`docs/COVERAGE_DESIGN.md` §4 + `docs/M2_MEMORY_PLAN.md`

三类记忆（**分离存储、分离召回**）：
- `preference` 偏好（风险承受度/风格/禁忌）—— **每次对话必注入**（小、固定）
- `fact` 事实（持仓/成本/长期计划/关注标的）—— **按当前问题涉及的标的召回**
- `experience` 经验（历史决策 + 事后结果）—— **向量召回 top-3**

契约（`tests/test_m2_memory.py` 已锁死，勿改语义）：
- 去重：`preference`/`fact` 按 `(kind, key)` 覆盖更新并**返回同一 id**；
  `experience` 的 key 是**内容指纹**（`fingerprint()`）—— 不能用 `''`（多条 `''` 在
  `UNIQUE(kind, key)` 下会互相冲突，Step 1 已实测确证）
- 删除必须**真生效**（B4）
- 无 Ollama/向量不可用 ⇒ `recall_experiences` **按时间倒序返回**且每条带 `embedded: False`，**不得抛**
- 隐私开关关闭 ⇒ `build_recall_block` 返回空串与全 0 计数（**不注入、不谎报**）
"""
import hashlib
import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from data import database as db

KIND_PREFERENCE = "preference"
KIND_FACT = "fact"
KIND_EXPERIENCE = "experience"
VALID_KINDS = (KIND_PREFERENCE, KIND_FACT, KIND_EXPERIENCE)

SETTING_MEMORY_ENABLED = "ai_memory_enabled"
DEFAULT_TOP_K = 3

_FALSEY = ("0", "false", "off", "no", "disable", "disabled")


# ======================================================================
# 基础工具
# ======================================================================
def fingerprint(content: str) -> str:
    """内容指纹：归一化（压空白 + strip + casefold）后取 sha1 前 16 位。

    归一化的意义：同一件事换个空格/大小写不该算两条（`test_experience_dedup_by_fingerprint`）。
    """
    norm = re.sub(r"\s+", " ", str(content or "")).strip().casefold()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def embed_text(text: str) -> Optional[bytes]:
    """把文本编码为向量字节（bge-m3）；不可用时返回 `None`（**不抛**）。

    ⚠️ 向量存 `memories.embedding`，**独立于 kb.db**（那是文档语料，不与用户决策史混库）。
    ⚠️ 降级是**正常路径**（无 Ollama 很常见），所以这里吞掉所有异常。
    """
    try:
        from utils.rag.embed import embed_texts_batched

        vecs = embed_texts_batched([str(text or "")])
        if not vecs:
            return None
        v = vecs[0]
        tobytes = getattr(v, "tobytes", None)
        if tobytes is None:
            return None
        import numpy as np

        return np.asarray(v, dtype="float32").tobytes()
    except Exception:  # noqa: BLE001 - 无 Ollama / 维度不符 / 任意环境问题 ⇒ 走降级
        return None


def _validate(kind: str, content: str) -> None:
    if kind not in VALID_KINDS:
        raise ValueError("kind 必须是 {} 之一，收到 {!r}".format(VALID_KINDS, kind))
    if content is None or not str(content).strip():
        raise ValueError("内容不得为空")


def _row(r) -> Dict[str, Any]:
    d = dict(r)
    try:
        d["meta"] = json.loads(d.get("meta") or "{}")
    except (ValueError, TypeError):
        d["meta"] = {}
    if "embedding" in d:
        d["embedded"] = d.pop("embedding") is not None
    return d


def _cols() -> str:
    return ("id, kind, key, content, meta, source, session_id, embedding, "
            "created_at, updated_at")


# ======================================================================
# 写入 / 读取 / 删除
# ======================================================================
def add(kind: str, content: str, key: str = "", meta: Optional[Dict[str, Any]] = None,
        source: str = "explicit", session_id: Optional[int] = None) -> int:
    """写入（或按去重规则覆盖）一条记忆，返回 id。

    - `preference` / `fact`：按 `(kind, key)` 覆盖 ⇒ 同 key 重复写入返回**同一 id**
    - `experience`：key 自动取**内容指纹**（调用方传的 key 被忽略）
    """
    _validate(kind, content)
    content = str(content).strip()
    if kind == KIND_EXPERIENCE:
        key = fingerprint(content)
    key = str(key or "")
    meta_json = json.dumps(meta or {}, ensure_ascii=False)

    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO memories(kind, key, content, meta, source, session_id) "
            "VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, key) DO UPDATE SET "
            "content = excluded.content, meta = excluded.meta, source = excluded.source, "
            "session_id = excluded.session_id, updated_at = CURRENT_TIMESTAMP",
            (kind, key, content, meta_json, str(source or "explicit"), session_id),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM memories WHERE kind = ? AND key = ?",
                           (kind, key)).fetchone()
        return int(row[0])
    finally:
        conn.close()


def propose(kind: str, content: str, key: str = "", meta: Optional[Dict[str, Any]] = None,
            session_id: Optional[int] = None) -> int:
    """写入**待确认候选**（§4.2 隐式：AI 不自行写记忆）。返回 pending id。"""
    _validate(kind, content)
    content = str(content).strip()
    if kind == KIND_EXPERIENCE:
        key = fingerprint(content)
    conn = db.get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO memories_pending(kind, key, content, meta, session_id) "
            "VALUES(?, ?, ?, ?, ?)",
            (kind, str(key or ""), content,
             json.dumps(meta or {}, ensure_ascii=False), session_id),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get(memory_id: int) -> Optional[Dict[str, Any]]:
    conn = db.get_conn()
    try:
        r = conn.execute(f"SELECT {_cols()} FROM memories WHERE id = ?",
                         (int(memory_id),)).fetchone()
        return _row(r) if r else None
    finally:
        conn.close()


def list_all(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = db.get_conn()
    try:
        if kind:
            rows = conn.execute(
                f"SELECT {_cols()} FROM memories WHERE kind = ? "
                "ORDER BY updated_at DESC, id DESC", (kind,)).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_cols()} FROM memories "
                "ORDER BY kind, updated_at DESC, id DESC").fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def delete(memory_id: int) -> bool:
    """删除单条；不存在的 id 返回 False（**不抛**）。"""
    conn = db.get_conn()
    try:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (int(memory_id),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_by_key(kind: str, key: str) -> bool:
    conn = db.get_conn()
    try:
        cur = conn.execute("DELETE FROM memories WHERE kind = ? AND key = ?",
                           (str(kind), str(key)))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ======================================================================
# 三类召回（§4.1：分离策略）
# ======================================================================
def recall_preferences() -> List[Dict[str, Any]]:
    """偏好：**全量**（小、固定）—— 每次对话必注入。"""
    return list_all(KIND_PREFERENCE)


def recall_facts(stock_codes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """事实：按标的过滤。

    - 传标的 ⇒ 返回「涉及这些标的的」+「无标的的通用事实」（如长期计划，始终相关）
    - 不传   ⇒ **只返回无标的的**（避免把无关个股信息注入当前问题）
    """
    rows = list_all(KIND_FACT)
    wanted = {str(c) for c in (stock_codes or []) if c}
    out = []
    for r in rows:
        code = (r.get("meta") or {}).get("code")
        if not code:
            out.append(r)                       # 通用事实（计划/风格）始终相关
        elif str(code) in wanted:
            out.append(r)
    return out


def _cosine(a: bytes, b: bytes) -> float:
    import numpy as np

    va = np.frombuffer(a, dtype="float32")
    vb = np.frombuffer(b, dtype="float32")
    if va.shape != vb.shape or va.size == 0:
        return -1.0
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va.dot(vb) / denom) if denom else -1.0


def recall_experiences(query: str, top_k: int = DEFAULT_TOP_K) -> List[Dict[str, Any]]:
    """经验：向量召回 top-k；向量不可用时**按时间倒序**返回并标注 `embedded: False`。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            f"SELECT {_cols()} FROM memories WHERE kind = ? "
            "ORDER BY created_at DESC, id DESC", (KIND_EXPERIENCE,)).fetchall()
    finally:
        conn.close()

    items = [_row(r) for r in rows]
    if not items:
        return []

    qv = embed_text(query)
    if qv is None:
        for it in items:
            it["embedded"] = False
        return items[:max(0, int(top_k))]

    scored = []
    for it in items:
        raw = None
        conn2 = db.get_conn()
        try:
            r2 = conn2.execute("SELECT embedding FROM memories WHERE id = ?",
                               (it["id"],)).fetchone()
            raw = r2[0] if r2 else None
        finally:
            conn2.close()
        if raw is None:
            it["embedded"] = False
            scored.append((None, it))
        else:
            it["embedded"] = True
            scored.append((_cosine(qv, raw), it))

    # 有向量的按相似度降序排在前面；无向量的按时间序附在后面
    with_v = sorted([s for s in scored if s[0] is not None],
                    key=lambda s: s[0], reverse=True)
    without = [s[1] for s in scored if s[0] is None]
    ordered = [it for _, it in with_v] + without
    return ordered[:max(0, int(top_k))]


def build_recall_block(question: str,
                       stock_codes: Optional[List[str]] = None
                       ) -> Tuple[str, Dict[str, int]]:
    """汇总为可注入文本 + 来源计数。

    ⚠️ 无命中或开关关闭 ⇒ `("", 全 0)`（不注入、不发事件、**不谎报**）。
    """
    empty = {"preferences": 0, "facts": 0, "experiences": 0}
    if not memory_enabled():
        return "", dict(empty)

    prefs = recall_preferences()
    facts = recall_facts(stock_codes)
    exps = recall_experiences(question or "", top_k=DEFAULT_TOP_K)
    n = {"preferences": len(prefs), "facts": len(facts), "experiences": len(exps)}
    if not any(n.values()):
        return "", dict(empty)

    lines: List[str] = []
    if prefs:
        lines.append("### 偏好（必读）")
        for p in prefs:
            lines.append("- {}".format(p["content"]))
    if facts:
        lines.append("### 相关事实")
        for f in facts:
            lines.append("- {}".format(f["content"]))
    if exps:
        lines.append("### 相关经验（历史决策与结果）")
        for e in exps:
            flag = "" if e.get("embedded") else "（未向量化，按时间序）"
            lines.append("- {}{}".format(e["content"], flag))
    lines.append("")
    lines.append("（以上为你的长期记忆；请自然使用，不要逐条复述，也不要在无记忆时假装记得。）")
    return "## 长期记忆\n" + "\n".join(lines), n


# ======================================================================
# 隐式候选（§4.2）
# ======================================================================
def list_pending() -> List[Dict[str, Any]]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, kind, key, content, meta, session_id, status, created_at "
            "FROM memories_pending WHERE status = 'pending' ORDER BY id DESC").fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def accept_pending(pending_id: int) -> Optional[int]:
    """接受候选 ⇒ 落入 `memories`（`source='implicit'`），返回新 id；不存在返回 None。"""
    conn = db.get_conn()
    try:
        r = conn.execute(
            "SELECT kind, key, content, meta, session_id FROM memories_pending "
            "WHERE id = ? AND status = 'pending'", (int(pending_id),)).fetchone()
    finally:
        conn.close()
    if not r:
        return None

    try:
        meta = json.loads(r["meta"] or "{}")
    except (ValueError, TypeError):
        meta = {}
    mid = add(r["kind"], r["content"], key=r["key"], meta=meta,
              source="implicit", session_id=r["session_id"])

    conn = db.get_conn()
    try:
        conn.execute("UPDATE memories_pending SET status = 'accepted' WHERE id = ?",
                     (int(pending_id),))
        conn.commit()
    finally:
        conn.close()
    return mid


def reject_pending(pending_id: int) -> bool:
    conn = db.get_conn()
    try:
        cur = conn.execute(
            "UPDATE memories_pending SET status = 'rejected' "
            "WHERE id = ? AND status = 'pending'", (int(pending_id),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


#: 显式记忆的触发词（§4.2：用户在对话中说「记住…」）
_REMEMBER_PAT = re.compile(r"记住|请记下|帮我记|记住我|remember", re.IGNORECASE)

#: 隐式抽取的 LLM 提示（B1 要求"用户说了偏好就落库"，不能只认「记住」字样）
_EXTRACT_PROMPT = """你是记忆抽取器。从下面这段对话里抽取**值得长期记住的用户信息**，输出 JSON 数组。

只抽用户自己陈述的：
- 风险承受度 / 投资风格 / 禁忌（kind="preference"）
- 持仓 / 成本 / 长期计划 / 关注标的（kind="fact"，meta 里可带 {"code":"600519"}）
- 历史决策 + 事后结果（kind="experience"，如「8 月加仓，回撤 12%」）

规则：
- **不要**抽寒暄、市场行情、AI 自己的回答、一次性的问题
- 每条格式 {"kind": "...", "content": "简洁的中文陈述句", "key": "短英文键（experience 可留空）"}
- 没有可抽的就输出 []

对话：
{conversation}

只输出 JSON 数组，不要任何解释。"""


def _default_llm_extract(messages: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """默认的 LLM 抽取实现（`call_llm` 不可用/格式异常时抛，由调用方降级到规则）。"""
    from utils import ai_helper

    convo = "\n".join("{}: {}".format(m.get("role"), m.get("content"))
                      for m in (messages or []) if m.get("content"))
    if not convo.strip():
        return []
    res = ai_helper.call_llm(messages=[{"role": "user",
                                        "content": _EXTRACT_PROMPT.format(conversation=convo)}],
                             tools=None)
    text = (res or {}).get("content") or ""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    data = json.loads(m.group(0))
    out = []
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "").strip()
        content = str(it.get("content") or "").strip()
        if kind in VALID_KINDS and content:
            out.append({"kind": kind, "content": content,
                        "key": str(it.get("key") or ""),
                        "meta": it.get("meta") if isinstance(it.get("meta"), dict) else None})
    return out


def summarize_to_candidates(session_id: Optional[int],
                            messages: Optional[List[Dict[str, str]]] = None,
                            llm_fn=None) -> int:
    """会话结束后抽取隐式候选，返回新增条数。

    - `llm_fn` 给了就用它；没给则**先尝试默认 LLM 抽取**（B1：用户陈述偏好即应落库），
      再退到**规则兜底**（只认「记住…」类显式意图）
    - ⚠️ 规则兜底刻意保守：宁可少抽，也不制造需要用户逐条驳回的噪声
    - ⚠️ 抽取失败（无 Key / 无网络 / 格式异常）**不得抛**，只是抽不到
    """
    if messages is None:
        messages = _load_session_messages(session_id)

    extractor = llm_fn or _default_llm_extract
    try:
        cands = extractor(messages) or []
        added = 0
        for cand in cands:
            if isinstance(cand, dict) and cand.get("content"):
                propose(cand.get("kind") or KIND_FACT, cand["content"],
                        key=cand.get("key") or "", meta=cand.get("meta"),
                        session_id=session_id)
                added += 1
        if added:
            return added
    except Exception:  # noqa: BLE001 - LLM 抽取失败即降级到规则
        pass

    added = 0
    for m in messages or []:
        if (m.get("role") or "") != "user":
            continue
        text = str(m.get("content") or "")
        for sent in re.split(r"[。！？\n；;]", text):
            if _REMEMBER_PAT.search(sent):
                cleaned = _REMEMBER_PAT.sub("", sent).strip(" ：:，,、")
                if len(cleaned) >= 2:
                    propose(KIND_PREFERENCE, cleaned, session_id=session_id)
                    added += 1
    return added


def _load_session_messages(session_id: Optional[int]) -> List[Dict[str, str]]:
    if not session_id:
        return []
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT role, content FROM agent_messages WHERE session_id = ? ORDER BY id",
            (int(session_id),)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# ======================================================================
# 隐私开关
# ======================================================================
def memory_enabled() -> bool:
    """是否允许 AI 使用长期记忆（默认**开**，与「允许 AI 读取我的持仓」保持一致）。"""
    val = db.get_setting(SETTING_MEMORY_ENABLED, "")
    if val is None or str(val).strip() == "":
        return True
    return str(val).strip().casefold() not in _FALSEY


def set_memory_enabled(on: bool) -> bool:
    return db.set_setting(SETTING_MEMORY_ENABLED, "1" if on else "0")
