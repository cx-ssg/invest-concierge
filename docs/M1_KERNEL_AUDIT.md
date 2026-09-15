# M1 内核切片 · 独立审计报告

> 任务：`m1-kernel-audit-20260915` ｜ brief：`D:/Vault/.reasonix/briefs/m1-kernel-audit.md`
> 审计方：critic 子代理（隔离上下文，只读）＋ 主 Agent 独立复核
> 日期：2026-09-15 ｜ 被审对象：`utils/rag/*`（6 模块）、`tests/test_rag_core.py`、`scripts/rag_probe.py`

---

## 一、结论判定

**REVISIONS_NEEDED → 已修订闭环（2026-09-15）**，critic 打分 **3/5**。

实现主体可用，但审计抓出 **1 处确凿自证陷阱 + 3 处静默失效路径 + 2 处契约漂移**。
全部 findings 均已处置（F7/F8 为明确遗留）。当前状态：**276 passed**，K1–K4 全绿。

---

## 二、findings（分级 + 证据 + 处置）

| # | 级别 | 现象 | 证据 | 处置 | 状态 |
|---|---|---|---|---|---|
| **F1** | 🔴 | **自证陷阱**：`test_rrf_fusion_merges_both_retrievers` 原断言 `set(order[:2]) == {0,2}` **在纯向量实现下同样成立**（语义序本就是 0,2,1）→ 无法区分「真融合」与「退化单路」 | `tests/test_rag_core.py:46`（旧版断言）；critic 独立指出 | 改为**精确断言 RRF 数值**，三种实现的期望值互不相同：纯向量 `(1/61, 1/62)` / 纯 BM25 `(1/62, 1/61)` / 真融合 `(1/61+1/62, 1/61)` | ✅ 已修 |
| **F2** | 🔴 | **降级路径把无关块排进 top-n**（critic F7）：`_top_k` 不过滤零分，池内「补位」把与查询无关的块塞进结果 | 原 `utils/rag/hybrid.py:32-35`；K3-b 实测输出 3 条全进（含完全无关块） | `_top_k` 增加 `min_score` 严格下限；语义路用相对阈值、BM25 路用 `>0` | ✅ 已修 |
| **F3** | 🔴 | **0.35 绝对阈值不成立**：无关查询的 `max_sim` 可达 0.381 > 0.35 → A3「无关查询返回 0 条」实际会失败 | 实测探针（bge-m3，5 篇样例）：量子计算=**0.381**、python异步=0.302；相关查询的相关块=0.72~0.80 | 改为**双判据**：绝对下限 `MIN_SIM=0.45` + 相对阈值 `MIN_SIM_RATIO=0.85`。`utils/rag/hybrid.py:17-25` | ✅ 已修（K4 验证 `NO_HIT`） |
| **F4** | 🟡 | **探针自身泄漏判据**：`rag_probe.py` 无条件传 `min_sim=0.0`，把 A3 判据一并废掉 → 无关查询也返回 3 条 | 原 `scripts/rag_probe.py:86` | 仅在**降级分支**归零判据；正常路径用默认值。文件内已留注释说明踩坑 | ✅ 已修 |
| **F5** | 🟡 | **契约漂移**：spec §3 未定义后来加入的 `query_vec` / `min_sim_ratio` 参数 | `docs/M1_KERNEL_SPEC.md:45-52` | spec §3 签名与判据说明已同步，并标注实测依据 | ✅ 已修 |
| **F6** | 🟡 | **契约漂移**：spec §4 第 2 条要求「超长表格按行切并重复表头」，实现**未做**（表格无上限） | `docs/M1_KERNEL_SPEC.md:65` vs 原 `utils/rag/chunker.py` | 新增 `_split_table_keeping_header()`（每段重复表头）＋ 回归测试 | ✅ 已修 |
| **F7** | ⚪ | **BM25 路无相对阈值** → 共享通用词（"增长"）的弱相关块会进入结果 | K2 实测：#2 为比亚迪块，与"茅台"查询仅共享"增长" | **刻意保留召回**，不臆造阈值；已写入 `M1_KERNEL_SPEC.md` §9，待真实语料接入后用标注数据决定 | 📋 遗留（已知） |
| **F8** | ⚪ | **`store.py` 无测试覆盖** | `tests/` 下无 `test_rag_store.py` | 本切片不覆盖（K1 不涉 store）；已在报告中显式登记，留待采集切片一并测 | 📋 遗留 |

---

## 三、审计的诚实边界

1. **critic 的完整报告未能落盘。** critic 子代理自陈「无 `write_file` 权限」，其 findings 全文未回传，
   仅给出结论摘要（REVISIONS_NEEDED / 3-5 分 / 1 处自证陷阱 + 3 处静默失效 + 契约漂移）。
   本报告 F1/F2/F5/F6 的**定位来自 critic 的结论方向**，但**具体证据与复算由主 Agent 独立完成**（未直接引用其原文）。
2. **critic 无法实跑。** 它自陈「无 bash 权限」，其提出的「274 passed 是否属实」这一质疑，由主 Agent 实跑确认：
   `pytest -p no:warnings` → **276 passed in 14.59s**（274 基线 + 2 条新增用例）。
3. **阈值是初值。** `MIN_SIM=0.45` / `MIN_SIM_RATIO=0.85` 基于 **5 篇样例**的 bge-m3 相似度分布，
   样本量不足，**尚未在真实公告/研报语料上验证**。已在 `COVERAGE_DESIGN.md` §3.3 与 `M1_KERNEL_SPEC.md` §9 标注需重测回填。
4. **未验证项**：真实语料的检索质量（需采集层）、`store.py` 的落库/加载正确性、`embed_texts` 的 300s 超时在万级块下的实际表现。
5. **本报告不是「独立审计已通过」的证明**：audit 抓出的问题由**主 Agent 自行修复**，
   修复结果**未经第二轮独立复核**（按交付前三问，这是明确缺口）。

---

## 四、复核证据（主 Agent 实跑）

| 项 | 命令 | 结果 |
|---|---|---|
| K1 | `pytest tests/test_rag_core.py -p no:warnings` | **12 passed** |
| 全量回归 | `pytest -p no:warnings` | **276 passed in 14.59s** |
| K2 | `python scripts/rag_probe.py` | `mode=hybrid hits=2`，#1 命中贵州茅台段 |
| K3 | `python scripts/rag_probe.py --no-embed` / `--embed-url http://127.0.0.1:1/api/embed` | 两条降级路径均 exit 0、不崩、给出 `ollama pull bge-m3` 指引 |
| K4 | `python scripts/rag_probe.py --query "量子计算最新进展"` | `hits=0` + `RESULT: NO_HIT`（A3 达标） |
