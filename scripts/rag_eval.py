# -*- coding: utf-8 -*-
"""检索评测器 —— 按 `tests/golden/rag/README.md` 的指标口径打分。

指标（替代旧口径的"可分 / 不可分"）：

- `A3a 域外主动弃权`    **主结论** —— 域外查询被主动弃权的比例（`out_of_domain` 应 = 1.000）
- `over_abstain_rate`   域内可答却被判 none —— **可答查询被剥夺引用凭据的比例**（A3c 召回护栏）
- `delegated_rate`      非 none 占比 —— ⚠️ 判官从未实现 ⇒ 这是**风险面**（"可被引用的错误断言"的参数），
                        **撤档（2026-09-18）后不再是"成本指标"**；补数 `abstain_recall = 1 − delegated_rate`
- `Recall@k` / `MRR@10` 答案块是否被召回、排多前（块级标注才有）
- `trusted_recall`      ⚠️ **派生量** ≡ `Recall@k − over_abstain`，不是独立测量

（旧口径的 `weak_fp_rate` 已删 —— weak 是**委派点**，不是失败；见 README 的标注规范。
 撤下 `strong` 档后，`strong_fp_rate` / `正例 strong 率` 一并**失去对象**，已从上表移除。）

用法：
  python scripts/rag_eval.py                    # holdout（⚠️ 已用于阈值选择 = **拟合集**，非干净验收组）
  python scripts/rag_eval.py --split tuning     # 调参（会打印警告）
  python scripts/rag_eval.py --scan             # 扫阈值：none 稠密网格 + SAR_NONE 敏感性表
  python scripts/rag_eval.py --judge llm        # B1 LLM 判官（仅 weak 档触发）→ judge_fp / 触发率 / 延迟
                                                # ⚠️ 真调 LLM，耗时随样本线性增长（--judge-max 可截断）

## 2026-10-03 F0a-2 · **两种口径并列**（不得用新口径替换旧口径）

`docs/M1_EVAL_REPORT.md` §4g/§4h 早已记录「**评测与被测对象解耦**」：本脚本原先只跑
`run_hybrid`（**不经 `retrieve_docs` / 不经 `query_scope`**）⇒ 输出的恒是**全库口径**，
不反映产线形态（F0a `c12d3e1` 修好的查询侧标的识别，评测里**根本走不到**）。现并列两条臂：

| 口径 | 代表什么 | 走什么路径 |
|---|---|---|
| `full` | 系统**无法识别标的**时的能力（旧口径，**保留不改**） | `run_hybrid` 直调 |
| `prod` | 用户**实际问某个标的**时的能力（产线形态） | `retrieve_docs`（经 `query_scope`） |

⚠️ 评测集的 27 条正例是**椭圆**查询（`每股能分到多少钱？`，不含标的）⇒ 直接送 `retrieve_docs`
会因识别不到标的而**退化成全库**（= `[prod·raw]` 一行，如实报出）。故 `prod` 臂按产线真实形态
**改写**查询：把样本答案所属标的的**公司名**前缀到 query 上 —— 依据是 F0a 真实链路实测
（`report-F0a.md` §1.2：LLM 喂给 `retrieve_docs` 的 query **一律带公司名**）。
**改写只动查询文本，不动语料 / 不改样本 / 不动金标 / 不动阈值**；`full` 口径同时并列报出。

## ⚠️⚠️ 2026-10-03 F-R1 · **`prod` 臂是「答案泄漏」的 oracle 上界，不是产线实测**（审计 F3 复核）

`prod_queries()` 的前缀**来自金标块所属的 `code`**（`answer_chunk_ids` → `meta[*].code`），
即**金标信息被写进了 query**。F-R1 实测（原始输出 `.fr1/fr1_fr2_prod_leak.txt`）：

```
  臂A  prod 改写（prefixed query, code=None→auto） Recall@5=0.926 MRR@10=0.781 混标的=0/27
  臂B1  同一段 prefixed query + 硬编码 code=600519  Recall@5=0.926 MRR@10=0.781 混标的=0/27
        ⇒ 两条臂 top-10 chunk_id 序列**逐位相同（27/27）**
  臂B2  原始椭圆 query + code=600519（F0a 口径②）  Recall@5=0.926 MRR@10=0.661（查询文本不同 ⇒ MRR 不同）
  口径③ full + prefixed query（不收窄）             Recall@5=0.889 MRR@10=0.758 混标的=7/27
```

⇒ `prod` 度量的是「**若已知标的**（金标码），检索能不能找到答案」= **带标签的上界**。
⇒ **净效应**只有 **0.889 → 0.926**（+0.037，混标的 7/27→0/27）；
`0.593 → 0.889` 是**查询形态效应**（椭圆 → 带标的），与 F0a 修复无关。
commit `c12d3e1` 的信息「Recall@5 0.593 → 0.926 恢复」把这两段混成一段 ⇒ **本轮订正**。
⇒ 引用 `[prod]` 时必须写成「**已知标的条件下的上界（oracle）**」；**不得**当作产线成绩。
真正的产线口径需要**真实 LLM 改写**（F0a 实测 LLM 时带时不带 `code`，那是另一件事）。
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rag import store as rag_store                      # noqa: E402
from utils.rag import evidence as ev_mod                      # noqa: E402
from utils.rag.embed import embed_texts_batched               # noqa: E402
from utils.rag.evidence import EvidenceJudge, LEVEL_NONE  # noqa: E402
from utils.rag.hybrid import MAX_PER_DOC_DEFAULT, run_hybrid  # noqa: E402
# 2026-10-03 F0a-2：产线形态评测臂要**经过被测对象**（`retrieve_docs` → `query_scope`）。
# ⚠️ 这两个 import 放在模块级（而非函数内）是为了让测试能 `monkeypatch.setattr(ev, ...)`
#    把整条产线路径换成替身 —— 与 `judge_metrics()` 里那个函数内 import 不同，那是历史写法。
from utils.rag.query_scope import company_names               # noqa: E402
from utils.rag.retrieve import retrieve_docs                  # noqa: E402
from utils.rag.tokenize import tokenize                       # noqa: E402

GOLDEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "tests", "golden", "rag")
TOP_K = 5


def load(split, key):
    path = os.path.join(GOLDEN, "queries_%s_%s.json" % (split, key))
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def assert_clean_holdout(rows_irr):
    """holdout 负例必须**全部** `batch="v2"`（= 从未参与调参）—— 给核心卖点加强制力。

    2026-09-17 独立审计 P2-2：`batch` 字段此前**无任何代码消费**，
    而「holdout 的 50 条负例全 v2」正是"干净验收组"的**全部依据**；
    没有这道门，未来把调参时看过的样本挪进 holdout，指标不会报警。
    """
    bad = [r.get("id") for r in rows_irr if r.get("batch") != "v2"]
    if bad:
        raise ValueError(
            'holdout 负例必须全部 batch="v2"（从未参与调参），以下不是：%s —— '
            "v1 样本曾用于定阈值，混入验收组会让验收失去意义" % bad)


def load_holdout():
    """holdout 的**唯一入口**：读取 + 强制校验（含拒绝空负例）。

    2026-09-18（审计 B 的 P2-1）：原先把校验只放在 `main()` 里 → 任何**绕过 CLI** 的调用方
    （直接 `load()`）都不受约束，审计实测传入 3 条 `batch=v1` 负例静默通过。
    更隐蔽的一条：`irr` 为空时断言放行 → `n_irr=0`、`by_kind={}`、`strong_fp=0.000`，
    且 `main()` 里 A3a 主结论那行因 `ood.get("n")` 为假**根本不打印** ——
    「没有负例、却看起来干净」的报告可以静默产出。故此处显式拒绝空集。
    """
    rel, irr = load("holdout", "rel"), load("holdout", "irr")
    # ⚠️ 2026-09-18 第六轮审计二 P1-2：**上一轮只堵了负例一侧 —— 正例一侧是镜像洞**。
    # `rel` 为空时 `n_rel = max(0, 1) = 1` ⇒ `Recall@5 = 0`、`trusted_recall = 0`、
    # `strong_rel_rate = 0`，**而 A3a 仍打印 `20/20 = 1.000 <<< 主结论`**，无异常、`EXIT=0`
    # —— 「没有正例、却看起来干净」的报告**仍可静默产出**，形状与上一轮修掉的那条完全一致。
    if not rel:
        raise ValueError("holdout 正例为空（queries_holdout_rel.json 缺失或为空）—— "
                         "空正例会让 Recall/trusted_recall 静默归零，而 A3a 仍显示满分")
    if not irr:
        raise ValueError("holdout 负例为空（queries_holdout_irr.json 缺失或为空）—— "
                         "不得产出「没有负例却看起来干净」的验收报告")
    assert_clean_holdout(irr)
    return rel, irr


def evaluate(rows_rel, rows_irr, judge, meta, matrix, qvecs, k=TOP_K,
             max_per_doc=MAX_PER_DOC_DEFAULT):
    """返回指标 dict。qvecs 与 rows 顺序一致（已批量 embed）。

    ⚠️ 这是**纯计算函数**：数据入口的干净性校验在 `load_holdout()`，不在这里
    （它无法区分 tuning / holdout，放进来会误伤 tuning 的 v1 负例）。

    `max_per_doc`：同文档限额，透传给两次 `run_hybrid`（裸检索 + 工具真实形态）。
    默认 = 产线默认值 `MAX_PER_DOC_DEFAULT`（2），**不改变既有行为**；
    传 `0` 或 `None` = **关闭限额**（`None` 是 `hybrid.py` 的「关闭」语义）。
    ⚠️ 2026-10-02 外部复验 R-2 补：此前无法关闭限额 ⇒ RELEASE_NOTES 表格的
    「行① 基线（旧语料、无限额 1.000 / 0.702）」**没有任何 CLI 复现路径**
    （旧语料也只能带限额跑出 0.952/0.690 —— 一个表里不存在的状态）。
    """
    mpg = None if max_per_doc in (0, None) else max_per_doc
    n_expect = len(rows_rel) + len(rows_irr)
    if len(qvecs) != n_expect:
        # ⚠️ 旧实现直接 `zip(rows, qvecs[...])` 配对 → 长度不匹配会**静默截断**，
        # 指标少算一部分却看不出来（2026-09-17 新增用例时真实踩到）。
        raise ValueError("qvecs 条数 %d 与查询总数 %d 不一致 —— 静默截断会算错指标"
                         % (len(qvecs), n_expect))
    # ⚠️ 2026-09-18 第六轮审计二 P3-1：这里原有 `chunk_pos = {m["chunk_id"]: i ...}`，
    # 但 `evaluate()` **从未使用它**（全仓仅此一处）→ 已删（死变量）。
    # 这类"看起来在岗、其实不在岗"的代码是 `load_holdout` 死代码的同族，一并清掉。

    over_abstain = 0
    recall_hits, trusted_hits, rr = 0, 0, []
    tool_recall_hits, hard_stop = 0, 0
    # 2026-10-03 F0a-2：**混入其它标的**的条数（top-k 里出现 >1 个 `code`）——
    # 与 `prod` 臂用**同一个定义**（`report-F0a.md` 的「混标的」列），两臂可逐位对照。
    # 该量此前只在 F0a 的一次性脚本里算过，评测器**从不报** ⇒ 污染只能靠人工脚本发现。
    contaminated = 0
    for r, qv in zip(rows_rel, qvecs[:len(rows_rel)]):
        ev = judge.assess(r["query"])
        # ⚠️ 2026-09-18 **撤下 `strong` 档**后不再统计「正例 strong 率」——
        # `LEVEL_STRONG` 已删、分档只出 none / weak，那个量因此**失去对象**。
        if ev.level == LEVEL_NONE:
            over_abstain += 1
            # ⚠️ 2026-09-17 **删掉了原地的 `continue`**（独立审计实测指出）：
            # 旧写法在 none 档直接跳过检索 → ① Recall/MRR 的**分子被少算**（分母仍是 n_rel）
            # ② 报告里的 `got_top5=[]` 被误读成「检索也失败了」，真相是该条 gold 排在 **rank 1**。
            # 三个量各司其职、不可互替：
            #   Recall@k       = 检索器本身能力（judge=None 的**裸检索**）
            #   trusted_recall = **判据采信过的召回**：gold 在 top-k **且** `level != none`
            #   over_abstain   = 闸门把可答查询标成 none 的比例
        order, _, _ = run_hybrid(r["query"], matrix, meta, k=max(k, 10),
                                 query_vec=qv, judge=None, max_per_doc=mpg)
        got = [meta[i]["chunk_id"] for i in order[:k]]
        gold = set(r.get("answer_chunk_ids") or [])
        if gold & set(got):
            recall_hits += 1
            if ev.level != LEVEL_NONE:
                trusted_hits += 1
        # ⚠️ 2026-09-18 第六轮审计二（**一处改动同时关掉 U10 与「评测绕过被测对象」**）：
        # 上面那次是 **`judge=None` 的裸检索**；而**工具的真实形态**是 `judge=judge`
        # （`retrieve_docs` 就是这么调的）。此前评测**从不经过它**，于是：
        #   ① **分层硬停**（`hybrid.py`：零 bigram 交集 → 物理回空）在评测里**永不触发**
        #      —— 它**不改变 `level`**（同一个 evidence 早退），而 `judge=None` 又绕开判据，
        #      所以覆盖率从 5/20 掉到 0，**没有任何指标会红**；
        #   ② 工具层的任何退化（返空 / 排序错 / `code` 过滤失效）离线指标**一个都不会动**。
        # 现在补跑一次真实形态，产出 `tool_recall`（经过被测对象的召回）
        # 与 `hard_stop_count`（硬停触发数，此前完全不可见）。
        # 当下 holdout 上 `tool_recall` **应当 == trusted_recall**（0 条正例被硬停）——
        # **一旦不等，就是真信号**。
        tool_order, _, _ = run_hybrid(r["query"], matrix, meta, k=max(k, 10),
                                      query_vec=qv, judge=judge, max_per_doc=mpg)
        if not tool_order:
            hard_stop += 1
        if gold & {meta[i]["chunk_id"] for i in tool_order[:k]}:
            tool_recall_hits += 1
        all10 = [meta[i]["chunk_id"] for i in order[:10]]
        rank = next((j + 1 for j, c in enumerate(all10) if c in gold), None)
        rr.append(1.0 / rank if rank else 0.0)
        # 混标的：top-k 的 `code` 集合 > 1（`code` 为 None 的块不参与判定，避免把
        # 「无标的元数据」误记成另一个标的）。
        top_codes = {meta[i].get("code") for i in order[:k]} - {None}
        if len(top_codes) > 1:
            contaminated += 1
        # ⚠️ 2026-09-18 **删除了 `guarded_recall`**（两份外部审计**各自实测**证明它恒等于 `Recall@k`）：
        #   删掉 none 档硬停后，`run_hybrid` 的 `judge` **只用于算 evidence、不参与任何过滤/排序**
        #   （构造性恒等）→「带闸门再跑一次」与「裸检索」返回同一个 order；
        #   审计 B 的证伪实验：把判据换成「恒返回 none」，`guarded_recall` **纹丝不动**（仍 1.000/0.571）。
        #   另：当时两侧池大小 `kk` 在 N≤100 时相同、N=150 起才会因**池大小**而非闸门出现差异 —— 该指标在
        #   任何规模上都不成立。替代量 `trusted_recall` 会随判据退化而变红（判官恒 none → 0/n_rel）。

    # 负例：**按 kind 分列**（2026-09-17 修正口径，外部评审指出）
    # ⚠️ 2026-09-19 第七轮审计一 P2-2：下面两行是**撤档前的口径**，与 20 行之后的
    # `delegated_rate` 注释**自相矛盾**（那里说 weak 是"风险面"）。已按撤档后重写：
    # - 撤档后**没有"违规 / 合规"之分**（`strong` 已不存在）—— 负例只有两种落点：
    #   `none`（判据主动弃权）与 `weak`（判据未通过、但**带完整 `url`/`title` 返回给模型**）；
    # - `weak` **不再是"可接受的委派点"** —— 那个说法预设了"判官会兜底"，而**判官从未实现**
    #   ⇒ 它是**暴露面**：负例落 weak 的比例就是 `delegated_rate`（见下方注释）。
    by_kind = {}
    for r, qv in zip(rows_irr, qvecs[len(rows_rel):]):
        lv = judge.assess(r["query"]).level
        d = by_kind.setdefault(r.get("kind", "unknown"),
                               {"n": 0, "none": 0, "weak": 0})
        d["n"] += 1
        d[lv] += 1

    delegated = sum(d["weak"] for d in by_kind.values())

    n_rel, n_irr = max(len(rows_rel), 1), max(len(rows_irr), 1)
    return {
        "n_rel": len(rows_rel), "n_irr": len(rows_irr),
        "over_abstain_rate": over_abstain / n_rel,
        # ⚠️⚠️ 2026-09-18 **`delegated_rate` 的语义变了**（撤下 strong 档的副作用，必记）：
        # 以前它是「**委派成本**」—— 落 weak 表示"交给 LLM 判官"，是可接受的中间态
        # （理由一直是"判官会兜底"）。**但判官从未实现**（全仓 grep `llm_judge|judge_evidence` 0 命中）
        # ⇒ 现在它描述的是「**未通过判据、却仍返回给模型的结果占比**」= **风险面**，
        # 不再是成本。**读它的时候不要再按"成本"理解。**
        "delegated_rate": delegated / n_irr,
        # ⚠️ 2026-09-19 第七轮审计一 U1：**新增 `abstain_recall`** ——
        # `tests/golden/rag/README.md` 的指标表**早就定义了它**（「应弃权的查询中，
        # 实际判 `none` 的比例」）却**从未实现** ⇒ 作者想给 `delegated_rate` 设上限时，
        # 缺的那个名字**规范里其实已经有了**。二者关系：**`abstain_recall ≡ 1 − delegated_rate`**（负例口径）。
        # ⚠️ **按 kind 读，不要只看总数**：`out_of_domain` 应当 = 1.0（当前 20/20 ✓），
        # 而 `in_domain_unanswerable` / `near_miss` 是**字面判据结构上做不到**的那两类 ——
        # 对它们**只报数、不设门**（设门 = 要求判据做超出其能力的事）。
        "abstain_recall": (len(rows_irr) - delegated) / n_irr,
        "by_kind": by_kind,
        "Recall@%d" % k: recall_hits / n_rel,
        # `trusted_recall`（2026-09-18 新增，替代已删的 `guarded_recall`）：
        # **判据采信过的召回** —— gold 在 top-k **且** `level != none`。
        # 与 `Recall@k` 之差 = 「检索到了但判据没采信」的比例（这才是能随判据退化变红的量：
        # 判官恒 none → 0/n_rel；而旧 `guarded_recall` 在同样场景下纹丝不动）。
        "trusted_recall": trusted_hits / n_rel,
        # ⚠️ 2026-09-19 第七轮审计一 P3-1 / P3-2：**这段注释与代码不一致，已对齐**。
        # ① **对照物错了**：`tool_recall` 应与 **`Recall@k`** 比（**同义** —— 都是"gold 是否在 top-k"），
        #    **不是** `trusted_recall` —— 后者额外要求 `level != none`（holdout 上 1.000 vs 0.905）。
        #    正文的打印早就改对了（见下方 `[!] tool_recall != Recall@k`），**只有注释漏改** ——
        #    留着它的风险不是当下出错，而是**下一位编辑者照旧注释把打印改回去**（附录 A #9 第三次）。
        # ② **它也是派生量**：`tool_recall = Recall@k − (被分层硬停吞掉的召回)/n_rel`，
        #    与 `trusted_recall` 同构 ⇒ **不要与 `Recall@k` 并列成两个独立证据**。
        #    它的**独有价值在"路径"**：它是唯一经过被测对象（`judge=judge` + 硬停）的那条。
        "tool_recall": tool_recall_hits / n_rel,
        "hard_stop_count": hard_stop,
        # ⚠️ 2026-09-18 **撤下 strong 档**后，`strong_rel_rate` / `strong_fp_rate` 一并移除 ——
        # 它们度量的档位已不存在（分档只出 none / weak）。
        # **这不是"指标消失"**：它们要回答的问题（"负例有没有被误放行"）现在**由
        # `delegated_rate` 全权承担** —— 撤档后任何非 none 的负例都落在 weak，
        # 而 weak 一律带警示语（不再有"跳过警示"的通道）。
        # 旧值（**仅存历史，勿再引用**）：holdout `strong_fp=0.000`、正例 strong 率 0.381。
        "n_over_abstain": over_abstain,
        "MRR@10": sum(rr) / n_rel if rr else 0.0,
        # 2026-10-03 F0a-2：**全库口径的混标的条数**（定义与 `prod` 臂一致）。
        # 它是「这个数字代表什么」的一部分：全库口径下 top-k 会跨标的（F0a 实测 23/27）。
        "contaminated_count": contaminated,
        "contamination_rate": contaminated / n_rel,
    }


# ============================================================================
# 2026-10-03 F0a-2 · **产线形态评测臂**（`prod`）—— 经过被测对象的那条路
# ============================================================================
# 为什么需要一条新臂：`evaluate()` 里两次 `run_hybrid` 都是**绕过被测对象**的
# （`docs/M1_EVAL_REPORT.md` §4g/§4h：「`rag_eval.py` 从不调用 `retrieve_docs`」）。
# F0a 把**查询侧标的识别**做进了 `retrieve_docs`，而评测走不到那里 ⇒ 修好的东西
# **没有任何指标能看见**（`full` 口径恒 0.593/0.386）。本臂直接调 `retrieve_docs`
# —— 与产线工具**同一个入口**（`retrieve_docs` → `query_scope.detect_targets` → 池过滤 → `run_hybrid`），
# 因此 `query_scope` 的退化（识别不出标的 / 误收窄池）会**真的改变指标**。

def prod_queries(rows_rel, meta):
    """把 27 条**椭圆**评测查询改写成「用户点名某个标的」的 **oracle** 形态（**模拟**）。

    ⚠️ **F-R1 复核：这不是产线口径。** 前缀的公司名由**金标块所属 `code`** 派生
    （`answer_chunk_ids` → `meta[*].code`）⇒ **答案信息被写进了 query**。
    实测（`.fr1/fr1_fr2_prod_leak.txt`）：同一段改写文本下，`code=None`（auto）与
    `code="600519"`（硬编码）两条臂的 top-10 chunk 序列**逐位相同（27/27）**
    ⇒ 它的 `Recall@5` 是「**已知标的**」条件下的**上界**，不是产线成绩。

    - 标的来源 = 该样本**答案块所属的 `code`**（`answer_chunk_ids` → `meta[*].code`），
      再由 `query_scope.company_names()` 取公司名（与 `query_scope` 里**同一套**数据驱动规则）。
    - **依据**：F0a 真实链路实测（`report-F0a.md` §1.2）—— LLM 喂给 `retrieve_docs` 的 query
      **一律带公司名**（`贵州茅台 分红方案 利润分配`），"用户实际问某个标的"就是产线常态；
      而评测集是椭圆查询（`每股能分到多少钱？`），直接送检索 **不含任何标的词**。
    - **边界**：改写只动 **query 文本**：语料（65 docs / 1691 chunks）、样本、金标块、阈值、
      评分函数全部不动。它度量的是「**查询形态已知标的**时的检索能力」，**不是**「修复涨了多少分」。
    - 识别不出标的 / 名称未知的样本**保持原样**（不收窄 —— 与产线 `code=None` 的兜底一致）。
    """
    by_id = {m.get("chunk_id"): m for m in (meta or [])}
    names = company_names(meta)
    out = []
    for r in rows_rel:
        code = None
        for cid in (r.get("answer_chunk_ids") or []):
            code = (by_id.get(cid) or {}).get("code") or code
            if code:
                break
        name = names.get(code)
        q = r.get("query") or ""
        out.append("%s：%s" % (name, q) if name and name not in q else q)
    return out


def evaluate_prod(rows_rel, meta, qvecs, db_path=None, queries=None, k=TOP_K):
    """**「已知标的」上界（oracle）**评测臂：`retrieve_docs`（经 `query_scope`）→ `Recall@5` / `MRR@10` / 混标的。

    ⚠️ 名字里的 `prod` 是历史叫法（F0a-2 时期）。F-R1 复核后口径订正为：**金标 code 派生查询**
    ⇒ 该臂是**上界估计**，不是产线实测（见模块头注与 `.fr1/fr1_fr2_prod_leak.txt`）。

    - `queries`：本次实际送进 `retrieve_docs` 的查询文本（默认 = `prod_queries()` 的**带标的**改写形态；
      传原样查询即得 `[prod·raw]` 退化诊断 —— 识别不到标的 ⇒ 与 `full` 几乎重合）。
    - `qvecs` 必须是**同一批 `queries`** 的向量（错位向量比报错更危险 ⇒ 这里显式校验条数）。
    - MRR 取 `retrieve_docs` 返回的 rank（`top_n=max(k,10)`），top-5 只用于 `Recall@k` 与混标的，
      与 `report-F0a.md` 的口径逐字一致（同一 `retrieve_docs`、同一个 `top_n=10`）。
    - ⚠️ **它才是"经过被测对象"的召回**：`evaluate()` 的 `Recall@k` / `tool_recall` 都只走
      `run_hybrid`，`query_scope` 的收窄与 `_payload` 的档位剥离在它们眼里**不存在**。
    """
    if queries is not None and len(queries) != len(rows_rel):
        raise ValueError("queries 条数 %d 与正例数 %d 不一致" % (len(queries), len(rows_rel)))
    if len(qvecs) != len(rows_rel):
        raise ValueError("qvecs 条数 %d 与正例数 %d 不一致 —— 静默错位会算错指标"
                         % (len(qvecs), len(rows_rel)))
    n = max(len(rows_rel), 1)
    hits5, rr, contaminated = 0, [], 0
    rows_out = []
    qs = queries if queries is not None else prod_queries(rows_rel, meta)
    for r, q, qv in zip(rows_rel, qs, qvecs):
        raw = json.loads(retrieve_docs(q, code=None, top_n=max(k, 10),
                                       db_path=db_path, query_vec=qv))
        results = raw.get("results") or []
        gold = set(r.get("answer_chunk_ids") or [])
        top5 = results[:k]
        hit = bool(gold & {x.get("chunk_id") for x in top5})
        if hit:
            hits5 += 1
        rank = next((x.get("rank") for x in results if x.get("chunk_id") in gold), None)
        rr.append(1.0 / rank if rank else 0.0)
        codes = {x.get("code") for x in top5} - {None}
        if len(codes) > 1:
            contaminated += 1
        rows_out.append({"id": r.get("id"), "query": q, "scope": raw.get("scope"),
                         "hit@%d" % k: hit, "codes": [x.get("code") for x in top5]})
    return {
        "n_rel": len(rows_rel),
        "Recall@%d" % k: hits5 / n,
        "MRR@10": sum(rr) / n,
        "contaminated_count": contaminated,
        "contamination_rate": contaminated / n,
        "rows": rows_out,
    }


# `SAR_NONE` 敏感性表的扫描点（2026-10-01 F1；**2026-10-03 F0b 用生产值 0.075 替换 0.06**）。**含当前值**，
# 其上界到 0.30 ——
# 再往上（0.40）**风险面已进入平台期**（tuning 0.141 / holdout 0.080，不再下降）而 oa 已 0.86~0.92
# —— 对决策无增量信息（见 --scan 的两组实测）。
# ⚠️ 扫描点集合必须**包含** `ev_mod.SAR_NONE`（`test_scan_returns_sar_none_sensitivity_grid` 锁）——
#     F0b 把生产值从 0.06 改到 0.08 时同步补入，否则 CLI 的 `<- 当前` 标记会永远打不出来。
def _fmt_thr(x):
    """阈值打印（**不能用 `%.2f`**）—— `SAR_NONE=0.075` 打成 `0.07` 会与真实值差 7%，
    且同屏的「当前」标记会指着一个不存在的扫描点（2026-10-03 F0b 实测踩到）。
    规则：最多 3 位小数、去掉尾随 0。"""
    s = "%.3f" % x
    return s.rstrip("0").rstrip(".") if "." in s else s


def _pct(values, p):
    """百分位（最近秩法；空集返回 0）。n 很小时 p50/p90 只是量级参考 —— 报告须带 n。"""
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return 0
    idx = int(round((p / 100.0) * (len(vals) - 1)))
    return int(vals[min(len(vals) - 1, max(0, idx))])


def _gold_in_candidates(o):
    """该行的 gold 块是否在**判官实际收到的候选窗**里（`judge_fn` 的归因分母）。

    ⚠️ 2026-10-03 **H2 口径订正**：旧实现用 `o["got_ids"]`（= `retrieve_docs` 返回的
    **全部**结果，`top_n=max(k,10)`）。但判官**拿不到全部** —— `judge_service.
    collect_from_tool_trace()` 会把候选截到 `JUDGE_MAX_CANDIDATES = 5`（与产线
    `retrieve_docs` 默认 `top_n=5` 对齐）。于是「归因」用的是**判官从未见过的窗口**：
    gold 排第 6~10 的行会被算成"判官看到了却没确认"，而判官连它的文本都没有。
    H2 实测（holdout，`gold_rank_top10` 为 6/8/9 的三条 `rel-0016/0029/0044`）
    ⇒ 这三条的真实归因是**窗口/检索**，不是判官误判。

    ⇒ 现在只用**判官实际收到的那几条**判断"gold 是否进了判官的窗"；
      「gold 是否被检索召回」另由 `_gold_in_retrieval` 报出（两个数字并列，不得互相替代）。
    缺标注 ⇒ 视为不可归因。
    """
    gold = o["row"].get("answer_chunk_ids") or []
    if not gold:
        return False
    return bool({str(g) for g in gold} & (o.get("fed_ids") or set()))


def _gold_in_retrieval(o):
    """该行的 gold 块是否在**本轮检索结果**里（诊断量：检索侧召回到没有）。"""
    gold = o["row"].get("answer_chunk_ids") or []
    if not gold:
        return False
    return bool({str(g) for g in gold} & (o.get("got_ids") or set()))


def judge_metrics(rows_rel, rows_irr, qvecs, k=TOP_K, db_path=None,
                  llm_fn=None, timeout_s=None, max_judge=0, top_n=None):
    """B1 · LLM 判官的评测口径（判据落 **`judge_fp`**，不在 `weak_fp`）。

    ## 候选窗（2026-10-03 F-R1 · 审计 F9/F-R5 修复）

    判官喂进去的候选块 = `retrieve_docs(query, top_n=top_n)["results"]`，
    `top_n` 缺省 = **`max(k, 10)`** —— 与 `evaluate_prod()` 的 `top_n` **完全一致**。
    修复前这里是 `top_n=k`（=5），而 `evaluate_prod` / `evaluate` 用 `max(k,10)`：
    两个「top-5」来自**不同候选池**（`hybrid.kk = min(max(k*20,50), N)` ⇒ kk=100 vs 200），
    于是 `judge_fn=0.192` 里的「检索没召回到」被系统性地多算：
    实测 6 条 fn 里 **3 条**（`rel-0016`/`0029`/`0044`，gold rank 6/9/9）只需把窗口提到 10
    就能进候选，**2 条**（`rel-0020`/`0021`，gold rank **1**）是判官真漏判，只有 **1 条**
    （`rel-0002`，rank 124）属真未召回 ⇒ 「修检索」只该留给 rank>100 的那一类。

    ⚠️ 与**产线**的差别（如实标注）：生产侧 `judge_service.JUDGE_MAX_CANDIDATES = 5`
    （判官只看 `retrieve_docs` 返回后截断的 5 条）。本参数对齐的是**评测器内部**两条臂的
    候选池，不是改产线判官窗口。

    ## ⚠️⚠️ 2026-10-03 **H2 定因**：F-R5 的窗口对齐**只做了一半**

    上面 `top_n=max(k,10)` 对齐的是**检索候选池**；但 `collect_from_tool_trace()` 会把
    **判官实际收到的**候选截到 `JUDGE_MAX_CANDIDATES = 5`。⇒ 判官**从未见过** rank 6~10 的块，
    而 `_gold_in_candidates()` 旧实现却拿 `top_n=10` 的结果当"判官看到了"：
    holdout 上 `rel-0016`/`rel-0029`/`rel-0044`（gold rank 6/8/9）被系统性算成"判官误判"。
    H2 实测（`.h2-scratch/probe_holdout_rel.jsonl` / `repeat_focus.log`）：
    **判官 8/8 次在这些行上说 irrelevant/uncertain，而 gold 一次都没喂给它**。
    ⇒ 归因已订正为**判官实际收到的窗**（`judge_fn_gold_recalled`），并另报
    `judge_fn_gold_retrieved`（检索窗）——两个数字并列，谁的责任一目了然。

    ## 口径（每个数字都必须带状态口径报出）

    - **触发条件** = 工具返回 `evidence_level == "weak"` **且有结果**；
      判定单点复用 `services.judge_service.collect_from_tool_trace`（**与 SSE 同源**，
      不在这里另写一份"什么算 weak"）。
    - `judge_fp` = **weak 档负例**里被判官放行（`level == "relevant"`）的比例。
      分母是 **weak 负例**，不是全部负例 —— `none` 档负例**根本不触发判官**（已弃权）。
      另有 `judge_fp_all_irr`：以全部负例为分母（`none` 档按"未放行"计）。
    - `judge_kill_rate` = **weak 档正例**里被判官判 `irrelevant` 的比例（误杀率）。
    - 引文未通过校验的 `relevant` **不计放行**：判官自己已把该条降级为 `uncertain`
      （`utils/rag/llm_judge.py` 的核心防线）⇒ `judge_fp` 只统计**通过校验**的放行。
    - 延迟取 `checked=True` 的轮次（未判出的轮次延迟没有意义）。

    ⚠️ **验收线不能定 0**：难例（`in_domain_unanswerable` + `near_miss`）首跑
    ≈ **0.29（n=14）**，且被放行那几条的引文**全部通过逐字校验**（`probe-judge-batch.json`）
    —— 引文校验挡不住"断章取义"。样本量小，勿过度推断。
    """
    from services import judge_service
    from utils.rag.retrieve import retrieve_docs

    n_total = len(rows_rel) + len(rows_irr)
    if top_n is None:
        # F-R1（审计 F9）：与 `evaluate_prod()` 的候选窗对齐（`max(k, 10)`），
        # 否则「judge_fn 里多少是检索没召回到」的归因建立在**更小的池**上。
        top_n = max(k, 10)
    observed = []
    for tag, rows, qs in (("rel", rows_rel, qvecs[:len(rows_rel)]),
                          ("irr", rows_irr, qvecs[len(rows_rel):])):
        for r, qv in zip(rows, qs):
            raw = retrieve_docs(r["query"], top_n=top_n, db_path=db_path, query_vec=qv)
            # ⚠️ 生产实况：`execute_ai_tool_v2` 对**返回字符串的工具**会再 `json.dumps` 一次
            #    （双层编码）⇒ 这里同样造双层，走的是与 SSE 完全相同的抽取函数。
            trace = [{"name": "retrieve_docs", "arguments": {"query": r["query"]},
                      "output": json.dumps(raw)}]
            reqs = judge_service.collect_from_tool_trace(trace)
            # F1：记下本次 top-k 的 chunk_id —— `judge_fn` 的**归因**要用它区分
            # 「判官误杀」与「gold 压根没被检索到」（两种原因的处置完全不同）。
            try:
                got_ids = {str(x.get("chunk_id"))
                           for x in (json.loads(raw) or {}).get("results") or []}
            except Exception:  # noqa: BLE001 - 记不到就退化为"不可归因"
                got_ids = set()
            observed.append({"tag": tag, "row": r, "req": reqs[0] if reqs else None,
                             "got_ids": got_ids,
                             # H2：判官**实际收到**的候选（`collect_from_tool_trace`
                             # 会截到 `JUDGE_MAX_CANDIDATES=5`）—— 归因必须用这个窗口
                             "fed_ids": {str(c.get("chunk_id"))
                                         for c in (reqs[0]["candidates"] if reqs else [])}})

    triggered = [o for o in observed if o["req"]]
    if max_judge > 0:
        triggered = triggered[:max_judge]
    # 评测**不设总预算**（逐行各自有界）：总预算是为"SSE 流有界"设计的，
    # 用在这里会把一整批评测掐成 timeout（口径污染）。
    events = judge_service.judge_rounds([o["req"] for o in triggered],
                                        llm_fn=llm_fn, timeout_s=timeout_s,
                                        max_rounds=len(triggered))
    for o, ev in zip(triggered, events):
        o["event"] = ev

    weak_irr = [o for o in triggered if o["tag"] == "irr"]
    weak_rel = [o for o in triggered if o["tag"] == "rel"]
    passed = [o for o in weak_irr if (o["event"] or {}).get("level") == "relevant"]
    killed = [o for o in weak_rel if (o["event"] or {}).get("level") == "irrelevant"]
    # ⚠️ 2026-10-03 F1：`judge_fn` / `span_valid` 是 A3b 判据（`docs/M1_EVAL_REPORT.md` L147）的
    #   另两个量，此前**本工具压根没实现** ⇒ 判据只被报出 1/3。口径（逐条可复算）：
    #   · `judge_fn`    = weak 档正例里**未被确认相关**的比例（`level != relevant`，含 `uncertain`）
    #     ⚠️ 它把「判官误杀」与「gold 根本没被检索到」**混在一起** —— 报告须同时给
    #        `judge_fn_gold_recalled`（分母只算 gold 在 top-k 内的那部分）用于归因。
    #   · `span_valid`  = 判官**提出** relevant 的条目里，引文通过逐字校验的比例。
    #     提出 = `verdict == relevant`（通过）**或** `quote_rejected == True`（被拒后已降级）；
    #     主体边界闸门降级的那批**不置** `quote_rejected` ⇒ 不计入跨度分母（它们不是跨度问题）。
    fn = [o for o in weak_rel if (o["event"] or {}).get("level") != "relevant"]
    # H2（2026-10-03）：两个归因口径**并列**报出 —— 不得只用其中一个解释 `judge_fn`：
    #   · `judge_fn_gold_recalled`  = gold 在**判官实际收到的窗**内（判官责任的**上界**）；
    #   · `judge_fn_gold_retrieved` = gold 在**本轮检索结果**内（判官看都没看到，归因检索/窗口）。
    fn_gold = [o for o in fn if _gold_in_candidates(o)]
    fn_retr = [o for o in fn if _gold_in_retrieval(o)]
    span_ok = span_bad = 0
    for o in triggered:
        for it in ((o["event"] or {}).get("items") or []):
            if it.get("verdict") == "relevant":
                span_ok += 1
            elif it.get("quote_rejected"):
                span_bad += 1
    grades = [o for o in triggered if (o["event"] or {}).get("checked")]
    latencies = [(o["event"] or {}).get("latency_ms") or 0 for o in grades]

    by_kind = {}
    for o in weak_irr:
        d = by_kind.setdefault(o["row"].get("kind", "unknown"), {"n": 0, "passed": 0})
        d["n"] += 1
        if (o["event"] or {}).get("level") == "relevant":
            d["passed"] += 1

    n_weak_irr, n_weak_rel = max(len(weak_irr), 1), max(len(weak_rel), 1)
    return {
        "n_total": n_total,
        "n_triggered": len(triggered),
        "judge_trigger_rate": len(triggered) / max(n_total, 1),
        "n_weak_irr": len(weak_irr),
        "n_weak_rel": len(weak_rel),
        "judge_fp": len(passed) / n_weak_irr,
        "judge_fp_all_irr": len(passed) / max(len(rows_irr), 1),
        "judge_kill_rate": len(killed) / n_weak_rel,
        "judge_fn": len(fn) / n_weak_rel,
        "judge_fn_n": len(fn),
        "judge_fn_gold_recalled_n": len(fn_gold),
        "judge_fn_gold_recalled": len(fn_gold) / n_weak_rel,
        "judge_fn_gold_retrieved_n": len(fn_retr),
        "judge_fn_gold_retrieved": len(fn_retr) / n_weak_rel,
        "span_valid": (span_ok / (span_ok + span_bad)) if (span_ok + span_bad) else 1.0,
        "span_proposed": span_ok + span_bad,
        "span_rejected": span_bad,
        "judge_uncertain": sum(1 for o in triggered
                               if (o["event"] or {}).get("checked") is False),
        "judge_fp_by_kind": by_kind,
        "latency_p50": _pct(latencies, 50),
        "latency_p90": _pct(latencies, 90),
        "n_latency": len(latencies),
        "judged_rows": [{"tag": o["tag"], "id": o["row"].get("id"),
                         "kind": o["row"].get("kind"),
                         "level": (o["event"] or {}).get("level"),
                         "checked": (o["event"] or {}).get("checked"),
                         "reason": (o["event"] or {}).get("reason"),
                         "latency_ms": (o["event"] or {}).get("latency_ms")}
                        for o in triggered],
    }


SAR_NONE_SWEEP = (0.06, 0.075, 0.10, 0.15, 0.20, 0.25, 0.30)


def scan(rows_rel, rows_irr, judge):
    """扫阈值，输出两张权衡表。

    **纪律**：两个池**互相留出** —— 在 tuning 上定值，用 holdout 报**一次**（反之亦然）；
    **绝不在同一个池上既选阈值又报成绩**。

    `weak_fp` 口径订正（2026-09-17）：旧版 `oa` 算的是"非 strong"（含 weak），
    与 `evaluate()` 的 `over_abstain`（只算 none）**不是同一个量** —— 两处口径必须一致。

    返回 `{"none": [...], "sar_none": [...]}`：
    - `none`：8×5 稠密网格 `(sar_none, v1_none, weak_fp, over_abstain)`
      （2026-10-03 F0b：sar 维加入生产值 0.08）
      `weak_fp` = 应弃权却**未判 none** 的比例（= 会进入生成上下文的暴露面）
      `over_abstain` = 域内可答却被判 none 的比例（召回护栏）
    - `sar_none`（**2026-10-01 F1 新增**）：把 `v1` 固定为 `V1_NONE`（实测 none 侧几乎由 SAR
      承担）后的 **7 行干净表** `(sar_none, 负例残留暴露, over_abstain)` ——
      **直接服务「要不要调高 `SAR_NONE`」**：调高会同时**压低**残留暴露、**抬高** oa
      （可答查询被剥夺引用凭据的比例）。**纪律：先定"可接受的引用丢失率"，再动 `SAR_NONE`。**
      ⚠️ 与 `none` 网格在共同点 `(sar_n, V1_NONE)` 上数值必须一致
      （交叉锁：`tests/test_rag_eval.py::test_sar_none_grid_matches_none_grid_at_v1_none`）。

    ⚠️ **历史**：此处原有一条「假想 strong 档曲线」。该档已于 2026-09-18 撤档（`7270159`），
    曲线所描述的对象**不存在** ⇒ 已删除（残留清理；计划 `docs/M1_F1_SAR_NONE_PLAN.md` §0）。
    """
    a_rel = [judge.assess(r["query"]) for r in rows_rel]
    a_irr = [judge.assess(r["query"]) for r in rows_irr]
    n_rel, n_irr = max(len(a_rel), 1), max(len(a_irr), 1)

    none_out = []
    for sar_n in (0.06, 0.075, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40):
        for v1_n in (0.35, 0.45, 0.55, 0.65, 0.75):
            irr_none = sum(1 for e in a_irr if ev_mod.is_none(e.sar, e.v1, sar_n, v1_n))
            oa = sum(1 for e in a_rel if ev_mod.is_none(e.sar, e.v1, sar_n, v1_n))
            none_out.append((sar_n, v1_n, (len(a_irr) - irr_none) / n_irr, oa / n_rel))

    # ⚠️ 独立计算（**不从 `none` 网格派生**）：将来改稠密网格的取值集合时，本表不得被静默改变。
    # 两处一致性由测试交叉锁（见 docstring）。
    # ⚠️ 2026-10-01（第八轮外部审计，双路实测）：闸门改走**共享判据** `ev_mod.is_none` ——
    # 此前这里用字面量重算，与生产判据零交叉锁（表内写死 0.30 时 344 条测试全绿）。
    sar_none_out = []
    for sar_n in SAR_NONE_SWEEP:
        irr_none = sum(1 for e in a_irr if ev_mod.is_none(e.sar, e.v1, sar_n, ev_mod.V1_NONE))
        oa = sum(1 for e in a_rel if ev_mod.is_none(e.sar, e.v1, sar_n, ev_mod.V1_NONE))
        sar_none_out.append((sar_n, (len(a_irr) - irr_none) / n_irr, oa / n_rel))
    return {"none": none_out, "sar_none": sar_none_out}


def main(argv=None):
    # 2026-09-18（审计 A 的 P2-1）：本脚本原先在 **GBK 控制台**上直接崩 ——
    # 新增的 `⚠️`(U+26A0) 无法用 cp936 编码 → `UnicodeEncodeError` + 退出码 1。
    # 这与本仓「UTF-8 铁律」自相矛盾（`print` 走平台默认编码）。双保险：
    # ① 这里 reconfigure；② 打印文本改用 ASCII 标记 `[!]`。
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["tuning", "holdout"], default="holdout")
    ap.add_argument("--db", default=None)
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--judge", choices=["none", "llm"], default="none",
                    help="B1：LLM 判官（**仅 weak 档触发**）→ judge_fp / 触发率 / 延迟分位")
    ap.add_argument("--judge-max", type=int, default=0,
                    help="判官最多判多少行（0=全部；成本控制用，会缩小分母并如实标注）")
    ap.add_argument("--judge-timeout", type=float, default=None,
                    help="单行判官的 LLM 调用上限（秒；缺省 = judge_service.JUDGE_TIMEOUT_S）")
    ap.add_argument("--max-per-doc", type=int, default=MAX_PER_DOC_DEFAULT,
                    help="同文档限额（每文档最多几块进 top-k）；0 = 关闭（复现旧行为）")
    ap.add_argument("--prod-raw", action="store_true",
                    help="额外报一行 [prod·raw]：原样椭圆查询走产线路径（识别不到标的 ⇒ 退化为全库）")
    args = ap.parse_args(argv)

    if args.split == "tuning":
        print("[eval] ⚠️ 本组已用于调参，**不得作为验收依据**（只能回答「阈值该定在哪」）")
    else:
        # ⚠️ 2026-09-18 第六轮审计二 P2-3：此处原印「验收组（未参与调参）」——
        # 但 holdout **已被用于选择 `SAR_STRONG`**（第五轮两份审计独立判定为违规）。
        # 报告 §4g 早已改口径，**横幅却还印着旧口径** —— 而横幅是使用者/审计方
        # **第一眼**看到的字符串。代码与文档的口径必须一致。
        # ⚠️ 2026-09-19 第七轮：**`strong` 档已撤下** ⇒ 原横幅末尾的"strong 档可达性"成了残句。
        # 但它**背后的污染事实仍然成立**（holdout 参与过阈值选择）⇒ 保留"拟合集"口径，
        # 只去掉已消失的对象。（**这是自查残留时发现的第 9 处，两份审计都没列。**）
        print("[eval] holdout —— ⚠️ **已用于阈值选择（拟合集）**，"
              "其上的阈值结论不得作为泛化依据")

    # ⚠️ 2026-09-18（审计二 P1-1 抓出）：**这里才是 `load_holdout()` 唯一的调用点**。
    # 我上一轮声称「`main()` 改走它」，但那个 edit 被工具拒绝后**我只补发了 reconfigure**，
    # 本行没改 → `load_holdout()` 成了**死代码**（全仓零生产调用点），
    # 而守护它的两条测试测的是**函数本身、不是调用链**，所以套件全绿、缺陷仍在。
    # 「保护从未被装上，而套件全绿」—— 这条教训比缺陷本身值钱。
    if args.split == "holdout":
        rel, irr = load_holdout()          # 读取 + assert_clean_holdout + **拒绝空集**
    else:
        rel, irr = load(args.split, "rel"), load(args.split, "irr")
    if not rel and not irr:
        print("[eval] 评测集为空 —— 先跑 scripts/rag_eval_build.py")
        return 2

    conn = rag_store.get_conn(args.db)
    meta, matrix = rag_store.load_index(conn)
    conn.close()
    if not meta or matrix is None:
        print("[eval] 索引为空 —— 先跑 scripts/rag_ingest.py")
        return 2

    judge = EvidenceJudge([tokenize(m.get("text") or "") for m in meta])
    qs = [r["query"] for r in rel] + [r["query"] for r in irr]
    qvecs = np.asarray(embed_texts_batched(qs), dtype="float32")

    m = evaluate(rel, irr, judge, meta, matrix, qvecs,
                 max_per_doc=args.max_per_doc)
    # 2026-10-03 F0a-2：**产线形态臂** —— 与 `full` **并列**报出，不替换。
    # `prod_queries()` 只改写 query 文本；`evaluate_prod()` 走的是产线工具入口 `retrieve_docs`。
    prod_qs = prod_queries(rel, meta)
    prod_qvecs = np.asarray(embed_texts_batched(prod_qs), dtype="float32")
    pm = evaluate_prod(rel, meta, prod_qvecs, db_path=args.db, queries=prod_qs)
    raw_pm = None
    if args.prod_raw:
        # 诚实边界：**原样**椭圆查询走同一条产线路径 —— 识别不到标的 ⇒ 退化成全库口径。
        raw_pm = evaluate_prod(rel, meta, qvecs[:len(rel)], db_path=args.db,
                               queries=[r["query"] for r in rel])
    print("[eval] chunks=%d 阈值：none 档 sar<%s v1<%s（**strong 档已撤下**：非 none 一律 weak）"
          % (len(meta), _fmt_thr(ev_mod.SAR_NONE), _fmt_thr(ev_mod.V1_NONE)))
    print("[eval] n_rel=%d n_irr=%d" % (m["n_rel"], m["n_irr"]))
    ood = m["by_kind"].get("out_of_domain") or {}
    if ood.get("n"):
        print("  A3a 域外主动弃权  = %d/%d = %.3f   <<< 主结论"
              % (ood["none"], ood["n"], ood["none"] / ood["n"]))
    print("  delegated_rate    = %.3f   (**非 none 占比** —— 判据未通过、但结果仍返回给模型；"
          % m["delegated_rate"])
    print("                                判官从未实现 ⇒ 这是**风险面**不是成本（2026-09-18 撤档后语义已变）)")
    print("  abstain_recall    = %.3f   (= 1 − delegated_rate；README 早已定义、**本轮才实现** ——"
          % m["abstain_recall"])
    print("                                ⚠️ **请按 kind 读**：out_of_domain 应为 1.0，另两类字面判据做不到)")
    print("  over_abstain_rate = %.3f   (%d/%d 判 none —— **闸门标注保守度**，不是召回损失："
          % (m["over_abstain_rate"], m["n_over_abstain"], m["n_rel"]))
    print("                                none 档不再无条件清空结果；零 bigram 交集仍硬停)")
    if m["over_abstain_rate"] > 0.20:
        # 2026-09-18（审计 B 的 P1-2）：修复后若干 headline 指标在「判据过度拒绝」时**全是满分**
        # （A3a 20/20、Recall 不变）—— 盲区是单向的。这里给一个下限告警。
        print("  [!] over_abstain_rate > 0.20 —— 闸门可能在过度拒绝（该方向上无其他指标会报警）")
    print("  --- 检索侧 ---")
    print("  Recall@%d          = %.3f   (judge=None **裸检索**能力 —— 旧版在 none 档 `continue`，分子被少算)"
          % (TOP_K, m["Recall@%d" % TOP_K]))
    print("  tool_recall       = %.3f   (judge 生效 + 分层硬停 —— 但它**仍只走 `run_hybrid`**，"
          % m["tool_recall"])
    print("                                 **不经 `retrieve_docs`/`query_scope`** ⇒ 不是产线入口；"
          "真正的产线入口见下方 [prod])")
    if abs(m["tool_recall"] - m["Recall@%d" % TOP_K]) > 1e-9:
        # ⚠️ 对照物是 **`Recall@k`**（**同义**：都是"gold 是否在 top-k"），**不是 `trusted_recall`** ——
        # 后者额外要求 `level != none`，两者**定义不同**，差值反映的是判据弃权、不是工具退化。
        # （第六轮审计二的建议原文写的是"应当等于 trusted_recall"，此处按定义更正。）
        # 本阈值下硬停通常为 0 ⇒ 二者**应当相等**；不等即「被测对象」与「裸检索」出现真实分歧。
        print("  [!] tool_recall != Recall@k —— **被测对象与裸检索出现分歧**（对照下方硬停计数）")
    print("  硬停触发          = %d/%d   (零 bigram 交集 → 物理回空；此前在评测里**完全不可见** ——"
          % (m["hard_stop_count"], m["n_rel"]))
    print("                                覆盖率从 5/20 掉到 0 也不会有别的指标变红)")
    print("  trusted_recall    = %.3f   (gold 在 top-k **且** 判据采信 —— 判据退化时它会变红；"
          % m["trusted_recall"])
    print("                                ⚠️ **派生量**：在**当前评测集上**数值恰好等于"
          " `Recall@k − over_abstain`")
    print("                                  —— 这是**经验巧合，不是构造性恒等**：被判 none 的正例"
          "若其 gold 不在 top-k 内，两者立刻背离（第八轮外部审计实跑反例证实）")
    print("                                  —— 判据退化时它会变红（对照用例："
          "tests/test_rag_eval.py::test_trusted_recall_turns_red_when_judge_degrades）")
    print("  MRR@10            = %.3f" % m["MRR@10"])
    # ========================================================================
    # 2026-10-03 F0a-2：**两种口径并列**（`full` 旧口径**原样保留** + `prod` 产线形态）
    # 数字含义必须写在数字旁：`full` ≠ `prod`，读到哪个数字都要知道它代表什么。
    # ========================================================================
    print("  --- 检索口径对照（两种口径**并列**，不得用新口径替换旧口径）---")
    print("  [full] 全库口径（`run_hybrid` 直调，**不经** `retrieve_docs`/`query_scope`）"
          " = 系统**无法识别标的**时的能力")
    print("         Recall@5 = %.3f  MRR@10 = %.3f  混入其它标的 = %d/%d  n=%d"
          % (m["Recall@%d" % TOP_K], m["MRR@10"], m["contaminated_count"],
             m["n_rel"], m["n_rel"]))
    print("  [prod] **已知标的条件下的上界（oracle）**：`retrieve_docs` **经** `query_scope`，"
          "但查询里的标的取自**金标块 code**（= 答案泄漏）")
    print("         它**不是产线实测** —— 它度量「若已知标的，检索能不能找到答案」；"
          "真正的产线需真实 LLM 改写（F-R1 复核见 .fr1/fr1_fr2_prod_leak.txt）")
    print("         Recall@5 = %.3f  MRR@10 = %.3f  混入其它标的 = %d/%d  n=%d"
          % (pm["Recall@%d" % TOP_K], pm["MRR@10"], pm["contaminated_count"],
             pm["n_rel"], pm["n_rel"]))
    print("  [!] 该口径的**净效应**只有 0.889→0.926（+0.037，混标的 7/27→0/27）；"
          "0.593→0.889 是查询形态效应（椭圆→带标的），与 F0a 修复无关")
    if raw_pm is not None:
        print("  [prod·raw] 同一条产线路径，但用**评测集原样查询**（椭圆、不含标的 ⇒ 识别不到 "
              "⇒ 退化为全库）：Recall@5 = %.3f  MRR@10 = %.3f  混入其它标的 = %d/%d"
              % (raw_pm["Recall@%d" % TOP_K], raw_pm["MRR@10"],
                 raw_pm["contaminated_count"], raw_pm["n_rel"]))
    print("  [prod] 改写只动 **query 文本**（语料 65 docs/1691 chunks、样本 27+113、金标块、阈值**全未动**）"
          "；`full` 口径**原样保留**，两个数字各自独立")
    if ood.get("n"):
        # F0a-2 任务书 §3：必须明写「A3a 不走检索 ⇒ 不受本口径新增影响」，防下游误读。
        print("  [!] A3a = %d/%d = %.3f —— **不走检索**（判据恒在**全库**上判定）"
              "⇒ **不受本次新增口径影响**"
              % (ood["none"], ood["n"], ood["none"] / ood["n"]))
    print("  --- 按 kind 分列（混池会互相抵消，必须分列看）---")
    for kind, d in sorted(m["by_kind"].items()):
        print("   %-26s n=%-3d none=%-3d weak=%-3d"
              % (kind, d["n"], d["none"], d["weak"]))

    if args.judge == "llm":
        # B1 判官：判据落 `judge_fp`（不在 `weak_fp`）。真调 LLM（成本随时间线性增长）。
        print("[judge] LLM 判官（B1）—— 口径：**仅 weak 档触发**；"
              "none 档已弃权、判官零调用（分母不含它们）")
        jm = judge_metrics(rel, irr, qvecs, k=TOP_K, db_path=args.db,
                           timeout_s=args.judge_timeout, max_judge=args.judge_max)
        if args.judge_max > 0:
            print("[judge] ⚠️ --judge-max=%d：**样本被截断**，以下比例的分母是截断后的子集"
                  % args.judge_max)
        print("  judge_trigger_rate = %.3f   (%d/%d 条查询落 weak 档且有结果 —— 判官的入口面)"
              % (jm["judge_trigger_rate"], jm["n_triggered"], jm["n_total"]))
        print("  judge_fp           = %.3f   (%d/%d **weak 档负例**被判官放行 —— "
              "分母是 weak 负例，不是全部负例)"
              % (jm["judge_fp"], round(jm["judge_fp"] * jm["n_weak_irr"]), jm["n_weak_irr"]))
        print("  judge_fp_all_irr   = %.3f   (以**全部负例**为分母；none 档不触发 ⇒ 按未放行计)"
              % jm["judge_fp_all_irr"])
        print("  judge_kill_rate    = %.3f   (%d 条 weak 档正例被判 irrelevant —— 相关查询误杀)"
              % (jm["judge_kill_rate"], round(jm["judge_kill_rate"] * jm["n_weak_rel"])))
        # F1：A3b 判据的另外两个量（此前本工具未实现 ⇒ 判据只被报出 1/3）
        print("  judge_fn           = %.3f   (%d/%d weak 档正例**未被确认相关** —— A3b 判据之一；"
              "含 uncertain)" % (jm["judge_fn"], jm["judge_fn_n"], jm["n_weak_rel"]))
        print("      └ 归因（H2 双口径，**必须并列读**）：")
        print("         · gold 在**判官实际收到的候选窗**内 = %d 条（= %.3f）⇒ 判官责任的上界"
              % (jm["judge_fn_gold_recalled_n"], jm["judge_fn_gold_recalled"]))
        print("         · gold 在**本轮检索结果**内 = %d 条（= %.3f）⇒ 其中差额是**判官压根没看到**"
              "（判官输入窗 = `judge_service.JUDGE_MAX_CANDIDATES`，产线为 5）"
              % (jm["judge_fn_gold_retrieved_n"], jm["judge_fn_gold_retrieved"]))
        print("         · 其余 = gold 连检索都没召回到（检索侧）")
        print("        （F-R1 对齐的是**检索候选池** top-%d；H2 实测判官仍只收到前 5 条 ⇒"
              " 归因不得用 top-%d 当判官的窗）" % (max(TOP_K, 10), max(TOP_K, 10)))
        print("  span_valid         = %.3f   (判官**提出**的 relevant 里引文逐字通过的占比；"
              "%d 条被拒 / 共 %d 条 —— A3b 判据之一)"
              % (jm["span_valid"], jm["span_rejected"], jm["span_proposed"]))
        print("  未判出（超时/解析失败）= %d 条；延迟 p50 = %dms  p90 = %dms（n=%d，仅计已判出）"
              % (jm["judge_uncertain"], jm["latency_p50"], jm["latency_p90"], jm["n_latency"]))
        for kind, d in sorted(jm["judge_fp_by_kind"].items()):
            print("   %-26s 判官放行 %d/%d" % (kind, d["passed"], d["n"]))
        print("  !! 验收线**不能定 0**：难例（in_domain_unanswerable + near_miss）首跑实测"
              " ≈0.29（n=14，probe-judge-batch.json），且有 4 条的引文**全部通过逐字校验**")
        print("     —— 引文校验挡不住「断章取义」；判官是增益，不是保证（样本量小，勿过度推断）")

    if args.scan:
        curves = scan(rel, irr, judge)
        print("[eval] --- none 档扫描 (sar_none, v1_none) -> "
              "(weak_fp=未判 none 的负例比例, over_abstain) ---")
        for sar_n, v1_n, wfp, oa in curves["none"]:
            flag = "  ← 当前" if (abs(sar_n - ev_mod.SAR_NONE) < 1e-9
                                  and abs(v1_n - ev_mod.V1_NONE) < 1e-9) else ""
            print("  sar<%s v1<%s -> weak_fp=%.3f oa=%.3f%s"
                  % (_fmt_thr(sar_n), _fmt_thr(v1_n), wfp, oa, flag))
        # ⚠️ 2026-10-01（F1）：此处原印「假想 strong 档曲线」—— 该档已于 2026-09-18 撤档，
        # 曲线描述的对象**不存在**（语义残留）⇒ 删除，位置让给**服务真实决策**的敏感性表。
        print("[eval] --- SAR_NONE 敏感性表（v1 固定 %s）—— 服务「要不要调高 SAR_NONE」 ---"
              % _fmt_thr(ev_mod.V1_NONE))
        print("  sar_none   负例残留暴露   oa(可答查询被剥夺引用凭据)")
        for sar_n, neg, oa in curves["sar_none"]:
            flag = "  <- 当前" if abs(sar_n - ev_mod.SAR_NONE) < 1e-9 else ""
            print("  %-7s    %.3f          %.3f%s" % (_fmt_thr(sar_n), neg, oa, flag))
        print("  -> 决策提示：先定「可接受的引用丢失率(oa)」，再动 SAR_NONE ——"
              " 两个方向都有真实代价，没有免费选项")
        print("  !  平台期：tuning 0.25 / holdout 0.15 之后风险面不再下降，继续调高只涨 oa")
    print("[eval] RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
