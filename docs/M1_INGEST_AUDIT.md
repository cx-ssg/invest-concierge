# M1 采集层（正文）与阈值回填 · 独立审计报告

> 任务：`m1-ingest-audit-20260916` ｜ brief：`D:/Vault/.reasonix/briefs/m1-ingest-audit.md`
> 审计方：critic 子代理（隔离上下文、只读）＋ 主 Agent 独立复核
> 日期：2026-09-16 ｜ 被审对象：`scripts/rag_ingest.py`、`utils/rag/embed.py`、`utils/rag/hybrid.py` 及配套测试

---

## 一、结论判定

**REVISIONS_NEEDED**（critic 计分 **3/5**）→ **本轮已修 4 项，余 1 项升级为架构决策（已拍板方案，待实施）**。

**最重要的结论不在 critic findings 里，而是主 Agent 复核时发现的**：

> **`MIN_SIM = 0.62` 是过拟合 —— 用从未参与定值的留出查询组复跑，两类分布根本不可分（区间倒挂）。**

| 查询组 | 相关查询 `max_sim` | 无关查询 `max_sim` | 判定 |
|---|---|---|---|
| TUNING（参与定值） | 0.6528 ~ 0.8191 | 0.4643 ~ 0.5864 | 可分（gap 0.067）→ 据此定了 0.62 |
| **HOLDOUT（未参与定值）** | 0.6668 ~ 0.8339 | **0.4542 ~ 0.6902** | **不可分**（IRR 上限 0.6902 > REL 下限 0.6668） |

即：**`critic` 的 F1 判断完全正确**，且实况比其推断更严重 —— 不是"阈值偏了"，而是**单靠向量相似度阈值无法实现 A3**。

---

## 二、findings 与处置

| # | 级别 | 现象 | 证据 | 处置 | 状态 |
|---|---|---|---|---|---|
| **F1** | 🔴 | **阈值循环论证（自证）**：`MIN_SIM=0.62` 取自 8 个查询的极值中点，而 K6 验收用的又是**同一批查询** → 不提供任何泛化证据 | `utils/rag/hybrid.py:17-25`；`scripts/rag_threshold_probe.py:28-39` | ✅ **已确证**：新增留出查询组（`--holdout`，4+4 条从未参与定值），复跑结果**不可分**（见上表）→ 方案升级为「BM25 主判据 + 阈值自动校准」（用户 2026-09-16 拍板 **A**） | 🔴 **确证，待实施** |
| **F2** | 🔴 | **`embed_texts_batched(batch_size<=0)` 静默丢向量**：`range(0, n, -1)` 为空 → 返回 `[]`，调用方误以为"没有向量要算"；`batch_size=0` 的 `ValueError` 无上下文 | `utils/rag/embed.py:36-45`（修复前） | 显式 `raise ValueError`；新增**条数一致性校验**（返回条数 != 请求条数 → `RuntimeError`，防止错位向量） | ✅ 已修（`tests/test_rag_embed.py` 2 条回归锁） |
| **F3** | 🟡 | **正文回退静默吞异常**：`except Exception: return None` 无日志、无区分 —— 无法分辨「本就无正文」与「接口挂了」 | `scripts/rag_ingest.py:105-106`（修复前） | 新增 `error_sink` 可选参数回报失败原因；`main()` 打印失败条数与样本 | ✅ 已修 |
| **F4** | 🟡 | **标题为空的记录被静默丢弃**（`continue` 无计数） | `scripts/rag_ingest.py:83-84`（修复前） | 新增 `skipped_sink` 参数回报跳过条数；`main()` 打印告警 | ✅ 已修 |
| **F5** | 🔴 | **空 pending 时 `vecs[0]` → IndexError**（全部公告切块为空时崩） | `scripts/rag_ingest.py`（修复前，`向量化完成` 打印处） | 新增 `if not pending:` 早退分支，返回 `NO_CHUNKS` | ✅ 已修 |
| **F7** | 🟡 | **测试自证/覆盖缺口**：embedding 测试全部 monkeypatch（锁契约非行为）；未覆盖 `batch_size` 边界、单批失败、「正文缺失仍落库」端到端链 | `tests/test_rag_embed.py` | 补 `batch_size<=0` 拒绝、条数不一致报错、`error_sink`/`skipped_sink` 回报共 5 条 | ✅ 部分（端到端链仍未覆盖，见 §三） |

---

## 三、审计的诚实边界

1. **critic 的报告正文未落盘**（它是只读子代理，无写权限）；本报告基于其**结论摘要**（VERDICT/SCORE + F1~F7 标题与位置）
   加上**主 Agent 的独立复核与实跑**重建 —— 未逐字引用其原文。
2. **F1 是主 Agent 复核时被实跑推翻的，不是 critic 直接证明的**：critic 只给出"循环论证、不提供泛化证据"的方法论判断；
   「留出组不可分」是主 Agent 用 `--holdout` 实跑得出的（原始输出：`REL 0.6668~0.8339 / IRR 0.4542~0.6902 / 可分=False`）。
3. **A3 当前处于"已知不达标"状态**：`MIN_SIM=0.62` 在 TUNING 组通过、在 HOLDOUT 组失败。
   **在方案 A 实施前，不应宣称 A3 已达成。**
4. **BM25 诊断同样是 8 个查询的小样本**（REL bm25_max ∈ [21.6, 48.4]、IRR ∈ [0, 7.3]），
   比值约 3 倍、远优于向量的倒挂，但**尚未在更大样本上验证**。
5. **未验证**：`store.save_embeddings` 长度错配行为、`chunk_document` 对极短回退文本的行为（critic 指出其不在 brief 读取白名单内）；
   「正文缺失仍落库」的端到端链仍只有 `build_text` 单元级背书；多标的语料下的阈值行为。
6. **已知局限**：语料为**单公司**（600519，814 块），语义基线偏高是本轮判据失效的直接诱因。

---

## 四、诊断数据（决定 A3 出路）

留出查询下，两路召回判据的可分性对比（814 块）：

| kind | 向量 `max_sim` | **BM25 最高分** | BM25 命中块数 | 查询 |
|---|---|---|---|---|
| REL | 0.8339 | **48.36** | 80 | 会计政策变更对利润的影响 |
| REL | 0.6668 | **26.70** | 71 | 风险评估报告的结论是什么 |
| REL | 0.7359 | **24.20** | 217 | 股东会审议通过了哪些议案 |
| REL | 0.6722 | **21.55** | 137 | 高级管理人员是否发生变动 |
| IRR | 0.4977 | **0.00** | 0 | 如何训练一个大语言模型 |
| IRR | 0.4542 | **1.56** | 1 | 世界杯决赛比分是多少 |
| IRR | 0.5503 | **7.31** | 41 | 北京到上海的航班时刻 |
| IRR | 0.6902 | **0.00** | 0 | 怎么写一封求职信 |

**读法**：向量列两行交错（无判别力）；BM25 列干净分离（约 3 倍 gap）。
⚠️ BM25 分数是**绝对值、随语料规模漂移**（idf 依赖 N）→ 不能写死常数门槛，故方案 A 要求**自动校准**。

---

## 五、待实施（用户已拍板方案 A，本轮按用户指示不改代码逻辑）

- [ ] `run_hybrid` 改为 **BM25 主判据 + 向量负责排序**；门槛**由语料分数分布自动校准**（不写死绝对值）
- [ ] `rag_threshold_probe.py` 的 TUNING / HOLDOUT 双组机制已就绪，改完后**两组都要跑**
- [ ] 重写 K6 验收口径：**验收必须用留出组**（用定值组验收等于自证）
- [ ] 扩语料（多标的 / 研报）后重测 —— 单公司同质语料是本轮失效的直接诱因

---

## 六、本轮复核证据（主 Agent 实跑）

| 项 | 命令 | 结果 |
|---|---|---|
| 全量回归 | `pytest -p no:warnings` | **303 passed** |
| 采集 + embedding 单测 | `pytest tests/test_rag_ingest.py tests/test_rag_embed.py` | **18 passed** |
| TUNING 组 | `python scripts/rag_threshold_probe.py` | `IRR<MIN_SIM<REL=True` |
| **HOLDOUT 组** | `python scripts/rag_threshold_probe.py --holdout` | **`可分=False`** ← 本轮最关键证据 |
| 正文采集 | `python scripts/rag_ingest.py --code 600519 --limit 20` | documents/chunks/embedded = **20/814/814** |
