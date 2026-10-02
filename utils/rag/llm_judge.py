# -*- coding: utf-8 -*-
"""LLM 判官 —— 纯逻辑层（引文确定性校验 + 输入裁剪 + 诚实降级）。

设计依据：`docs/COVERAGE_DESIGN.md` §3.3 判据部分 + 黑板 M1 段「④ LLM 判官
（异步 + 仅 weak + 引文确定性校验），判据落在 `judge_fp`（不在 `weak_fp`）」。
任务书：`task-B1.md` §1.1。

## 为什么必须做（实测，不是推测）

字面判据（SAR/V1）在**结构上**无法区分「域内不可答」与「相关」：`sar<0.15` 时
**83% 的相关查询被误弃权**、`sar<0.20` 时 **100%**（`docs/M1_EVAL_REPORT.md` §4b）。
`near_miss` 的词面全是合法域内词汇，缺的不是词而是**言语行为** ⇒ 只有 LLM 能判。
`weak` 档因此是判官的**入口**（`none` 档**不触发**，见下）。

## 三条硬规则（任务书 §1.1）

1. **引文确定性校验（本任务的核心防线）**：判 `relevant` 必须附 **≥12 字逐字引文**；
   代码把引文与候选 `text` 都做**空白折叠**（连续空白 → 单空格，见 `normalize_ws`）
   后做 substring 校验 —— **引用不存在 ⇒ 该条降级为 `uncertain` 并置
   `quote_rejected=True`**（不允许"看起来像"）。
   ⚠️ 这条校验**只能证明"非凭空编造"，不能证明"足以回答"**（B-F10：≥12 字阈值几乎
   没有鉴别力；B-F6：难例实测放行且引文全部通过校验）—— 详见 `quote_verified` docstring。
2. **输入裁剪**：每块只喂 `title + text[:per_chunk_chars]`；总 prompt 有上限。
   原型 43.7s 里 **90% 是 prefill** ⇒ 裁剪是主杠杆（实测裁剪后 2.9s，`probe-judge.json`）。
3. **诚实降级**：无 LLM / 超时 / JSON 解析失败 / 模型乱答 ⇒ `level="uncertain"`,
   `checked=False`，**绝不抛异常**。判官**永远不能**把不可信结果伪装成 `relevant`。

## 实测边界（`probe-judge-batch.json`，n 小，勿过度推断）

在真实 holdout 上：`out_of_domain` 放行 **0/10**，但
`in_domain_unanswerable` + `near_miss` 放行 **4/14 = 0.286**，且那 4 条的引文
**全部通过逐字校验** ⇒ **引文校验挡不住"断章取义"**：模型能引用真实存在、
但**不足以回答问题**的片段。相关查询误杀 1/5。**判官是增益，不是保证** ——
任何对外表述不得写成"判官能保证不误用"。

## 与产线的边界（硬约束，任务书 §3）

- **不参与检索排序/过滤**：本模块只产出「可信度标注」，`retrieve.py` / `hybrid.py`
  对本模块**零引用**（回归锁：`tests/test_rag_llm_judge.py::test_judge_does_not_participate_in_retrieval`）。
- **不改 `none` 档行为**：触发判定在 `services/judge_service.py`，只认 `weak`。
- **不新建 client**：`llm_fn` 缺省时复用 `utils.ai_helper.call_llm`（晚绑定，可 patch）。
"""
import json
import re
import threading
import time

__all__ = [
    "LEVEL_RELEVANT", "LEVEL_IRRELEVANT", "LEVEL_UNCERTAIN",
    "MIN_QUOTE_CHARS", "DEFAULT_PER_CHUNK_CHARS", "DEFAULT_MAX_TOTAL_CHARS",
    "normalize_ws", "quote_verified", "build_prompt", "judge_candidates",
]

LEVEL_RELEVANT = "relevant"
LEVEL_IRRELEVANT = "irrelevant"
LEVEL_UNCERTAIN = "uncertain"

#: 判 `relevant` 所需的最小引文长度（归一化后计）
MIN_QUOTE_CHARS = 12
#: 每块只喂前 N 字（裁剪 = 延迟主杠杆，实测 2.9s）
DEFAULT_PER_CHUNK_CHARS = 800
#: 整条 prompt 的字符上限（含模板与全部候选块）
DEFAULT_MAX_TOTAL_CHARS = 12000
#: 单次 LLM 调用的默认上限（秒）
DEFAULT_TIMEOUT_S = 20.0

#: 判官提示词。**只输出 JSON**（实测模型能稳定遵守，无需 function calling / JSON mode）。
PROMPT_TMPL = """你是检索质量判官。给定一个问题与若干候选文档片段，判断**这些片段能否回答该问题**。

问题：{question}

候选片段：
{candidates}

要求（严格遵守）：
1. 对每条候选给出 verdict：`relevant`（该片段直接包含能回答问题的信息）或 `irrelevant`。
2. 若判 `relevant`，**必须**附一段 **12 字以上的逐字引文** `quote`，且该引文必须**原样出现在该片段中**
   （不得改写、不得拼接、不得跨段取）。
3. 只输出 JSON，不要任何解释文字。格式：
{{"items":[{{"chunk_id":<int>,"verdict":"relevant|irrelevant","quote":"<逐字引文或空串>"}}]}}
"""

_WS_RE = re.compile(r"\s+")


class _LLMTimeout(Exception):
    """判官单次 LLM 调用超时（有界等待，不杀线程）。"""


def normalize_ws(s):
    """空白归一化（引文比对用）：把**连续空白折叠成单个空格**，并去掉首尾空白。

    ⚠️ 2026-10-03（B-R1 · 审计 B-F10）**语义已收窄**：旧实现是 `re.sub(r"\\s+", "", s)`
    —— 删掉**全部**空白 ⇒ 原文里被空白隔开的两段可以拼成一条"逐字"引文：

        quote_verified("aabbccddeeff", "aa bb cc dd ee ff")   # 旧实现 True，而原文并不连续

    现在只折叠连续空白（`"a  b"` → `"a b"`，`"ab"` 仍是 `"ab"`）⇒ **跨空白拼接会被拒**。
    代价（如实记录，属保守侧）：模型把原文里**不存在**的空白插进引文（原文
    "贵州茅台上半年"、引文 "贵州茅台 上半年"）会判不通过 —— 该方向只会把 `relevant`
    降级为 `uncertain`，**不会放行伪造内容**。换行 ↔ 空格这类"原文本来就有空白"的
    形态变化仍然容忍（模型抄回原文的常见行为）。
    """
    return _WS_RE.sub(" ", s or "").strip()


def quote_verified(quote, text, min_chars=None):
    """引文是否**空白折叠后逐字**出现在候选正文里（substring）。

    ## 这条校验**能证明**什么

    - 引文（≥ `min_chars` 字）确实是候选正文里的**一段连续文本**（空白折叠后逐字命中）；
    - ⇒ 判 `relevant` 的那条**不是凭空编造**的：模型没有虚构一段原文里不存在的话。

    ## **不能**证明什么（对外表述必读，审计 B-F6/B-F10）

    - **不证明引文足以回答问题**：模型可以引一段真实存在、但**断章取义**的片段
      （实测 4/14 难例被放行且引文全部通过校验，`probe-judge-batch.json`）；
    - **不证明结论正确**：`MIN_QUOTE_CHARS=12` 几乎不构成鉴别力 —— 任何 ≥12 字的原文
      子串都能过（它是"非编造"闸门，**不是"质量"判据**）；
    - **不覆盖改写**：同义改写会被拒（拒绝方向，不影响安全性质）；
    - **不覆盖跨空白拼接**（B-F10 加固后）：拼接处只要原文没有空白就会被拒。

    参数：`min_chars` 缺省 = `MIN_QUOTE_CHARS`（12）——太短的引文没有鉴别力，且"拼一个字"
    会绕过校验。**绝不抛异常**；非字符串按空串处理。
    """
    if min_chars is None:
        min_chars = MIN_QUOTE_CHARS
    q = normalize_ws(quote if isinstance(quote, str) else "")
    if len(q) < max(1, int(min_chars or 0)):
        return False
    return q in normalize_ws(text if isinstance(text, str) else "")


def _extract_json_object(text):
    """从模型文本里取第一个**括号配平**的 JSON 对象；找不到返回 None。

    比 `re.search(r"\\{[\\s\\S]*\\}")`（原型写法）稳：模型在 JSON 后补充说明、
    或引文里带 `{`/`}` 时，贪心正则会把尾巴一起吞进来导致解析失败。
    逐层扫描并**逐个起点重试**，任一候选串坏掉不影响后面的起点。
    """
    if not text:
        return None
    s = str(text)
    start = s.find("{")
    while start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except Exception:  # noqa: BLE001 - 该起点不是合法 JSON，试下一个
                        break
        start = s.find("{", start + 1)
    return None


def _extract_text(raw):
    """从 `llm_fn` 的返回里取出**模型文本**。

    ⚠️ 实测（`task-B1.md` §1.0 末行）：`utils.ai_helper.call_llm` 返回的是**包了一层**的
    `{"type": "text", "content": "<模型文本>", "usage": {...}}`。**必须取 `.content`**；
    否则会把外层对象当模型输出 → 解析到 0 条 → 判官形同失效（监督者首探针就踩过）。
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        content = raw.get("content")
        if isinstance(content, str):
            return content
        return json.dumps(raw, ensure_ascii=False, default=str)
    return "" if raw is None else str(raw)


def _resolve_llm(llm_fn):
    """解析实际调用的 LLM 函数：显式 `llm_fn` > `utils.ai_helper.call_llm`（晚绑定）。

    晚绑定（函数内 import + 取属性）让 `monkeypatch.setattr(ai_helper, "call_llm", ...)`
    与产线同路径生效；解析失败返回 None ⇒ 调用方降级（**不新起 client**，硬约束）。
    """
    if llm_fn is not None:
        return llm_fn if callable(llm_fn) else None
    try:
        from utils import ai_helper
        fn = getattr(ai_helper, "call_llm", None)
        return fn if callable(fn) else None
    except Exception:  # noqa: BLE001 - 判官是旁路，解析失败一律降级
        return None


def _call_bounded(fn, prompt, timeout_s):
    """有界等待地调用 `fn(prompt)`；超时抛 `_LLMTimeout`（线程不杀，daemon 自行收尾）。"""
    if not timeout_s or timeout_s <= 0:
        return fn(prompt)
    box = {}

    def _run():
        try:
            box["value"] = fn(prompt)
        except BaseException as e:  # noqa: BLE001 - 原样带回主线程再降级
            box["error"] = e

    t = threading.Thread(target=_run, daemon=True, name="rag-judge-llm")
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise _LLMTimeout("判官 LLM 调用超过 %.1fs" % timeout_s)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def build_prompt(question, candidates, per_chunk_chars=DEFAULT_PER_CHUNK_CHARS,
                 max_total_chars=DEFAULT_MAX_TOTAL_CHARS):
    """组装判官 prompt（返回 str）。

    - 每块只喂 `title + text[:per_chunk_chars]`（规则 2：裁剪）；
    - 整条 prompt（含模板）不超过 `max_total_chars`；超预算的候选**不喂**，
      由 `judge_candidates` 标成 `uncertain`（绝不假装判过）。
    """
    skeleton = PROMPT_TMPL.format(question=question, candidates="")
    budget = max(0, int(max_total_chars) - len(skeleton))
    blocks, used = [], 0
    for c in candidates:
        cid = c.get("chunk_id")
        title = c.get("title") or ""
        text = (c.get("text") or "")
        if not isinstance(text, str):
            text = str(text)
        block = "[chunk_id=%s] title=%s\n%s\n" % (cid, title, text[:per_chunk_chars])
        if used + len(block) > budget:
            break
        blocks.append(block)
        used += len(block)
    return PROMPT_TMPL.format(question=question, candidates="\n".join(blocks))


def judge_candidates(question, candidates, *, llm_fn=None,
                     per_chunk_chars=DEFAULT_PER_CHUNK_CHARS,
                     timeout_s=DEFAULT_TIMEOUT_S,
                     max_total_chars=DEFAULT_MAX_TOTAL_CHARS):
    """判官主入口（纯逻辑，可单测）。返回：

    ```
    {"level": "relevant"|"irrelevant"|"uncertain",
     "items": [{"chunk_id": ..., "verdict": ..., "quote": ..., "quote_rejected": bool}],
     "checked": bool, "reason": str, "latency_ms": int}
    ```

    语义：
    - `checked=False` ⇒ 判官**没有**产出可信结论（无候选 / 无 LLM / 超时 / 解析失败 /
      上游异常），`items` 为空，`reason` 说明原因；
    - `checked=True` ⇒ 模型输出已解析；`items` **对每条候选都有落点**
      （模型漏答 / 超预算 / 引文被拒 ⇒ `uncertain`，不得当"无关"也不得当"相关"）；
    - `level` 是整体档位：任一 `relevant` ⇒ `relevant`；否则有 `uncertain` ⇒ `uncertain`；
      全 `irrelevant` ⇒ `irrelevant`。

    **绝不抛异常**。
    """
    t0 = time.time()
    out = {"level": LEVEL_UNCERTAIN, "items": [], "checked": False,
           "reason": "", "latency_ms": 0}
    cands = [c for c in (candidates or []) if isinstance(c, dict)]
    if not cands:
        out["reason"] = "no_candidates"
        out["latency_ms"] = int((time.time() - t0) * 1000)
        return out

    fn = _resolve_llm(llm_fn)
    if fn is None:
        out["reason"] = "no_llm"
        out["latency_ms"] = int((time.time() - t0) * 1000)
        return out

    prompt = build_prompt(question, cands, per_chunk_chars, max_total_chars)
    try:
        raw = _call_bounded(fn, prompt, timeout_s)
    except _LLMTimeout:
        out["reason"] = "timeout"
        out["latency_ms"] = int((time.time() - t0) * 1000)
        return out
    except Exception:  # noqa: BLE001 - 上游任何异常都降级，不打断主链路
        out["reason"] = "llm_error"
        out["latency_ms"] = int((time.time() - t0) * 1000)
        return out

    parsed = _extract_json_object(_extract_text(raw))
    model_items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(model_items, list):
        out["reason"] = "parse_error"
        out["latency_ms"] = int((time.time() - t0) * 1000)
        return out

    by_id = {}
    for mi in model_items:
        if not isinstance(mi, dict):
            continue
        key = str(mi.get("chunk_id"))
        if key not in by_id:          # 重复 id 取首次
            by_id[key] = mi

    items = []
    for c in cands:
        cid = c.get("chunk_id")
        text = c.get("text") or ""
        if not isinstance(text, str):
            text = str(text)
        mi = by_id.get(str(cid))
        if mi is None:
            # 模型漏答 / 超预算未喂 ⇒ 未确认（不替模型编结论）
            items.append({"chunk_id": cid, "verdict": LEVEL_UNCERTAIN,
                          "quote": "", "quote_rejected": False})
            continue
        verdict = str(mi.get("verdict") or "").strip().lower()
        if verdict not in (LEVEL_RELEVANT, LEVEL_IRRELEVANT):
            verdict = LEVEL_UNCERTAIN
        quote = mi.get("quote") if isinstance(mi.get("quote"), str) else ""
        rejected = False
        if verdict == LEVEL_RELEVANT and not quote_verified(quote, text):
            # ★ 核心防线：引文不存在 ⇒ 降级 + 留痕，不允许"看起来像"
            verdict = LEVEL_UNCERTAIN
            rejected = True
        items.append({"chunk_id": cid, "verdict": verdict,
                      "quote": quote, "quote_rejected": rejected})

    if any(i["verdict"] == LEVEL_RELEVANT for i in items):
        level = LEVEL_RELEVANT
    elif any(i["verdict"] == LEVEL_UNCERTAIN for i in items):
        level = LEVEL_UNCERTAIN
    else:
        level = LEVEL_IRRELEVANT

    out.update({"level": level, "items": items, "checked": True, "reason": ""})
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out
