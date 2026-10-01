# F1 · `SAR_NONE` 敏感性表 —— 实施计划

> 生成：2026-10-01 ｜ 上游：`Handoff/2026-09-20-invest-concierge-M1-七轮审计-完整蒸馏.md` §7 ①
> 口径：TDD（RED → Verify RED → GREEN → Verify GREEN）｜每步含**精确命令 + 预期输出**
> 状态：设计已确认（用户 2026-10-01 拍板：删除旧曲线、扫描点取 6 行）

---

## 0. 为什么做（决策背景）

`SAR_NONE` 从 0.06 往上调会**同时**发生两件事，而当前 `--scan` 的输出**看不出这条权衡**：

| SAR_NONE | 负例残留暴露（风险面） | oa（可答查询被剥夺引用凭据） |
|---|---|---|
| **0.06（当前）** | **0.459** | **0.000** |
| 0.10 | 0.271 | 0.615 |
| 0.15 | 0.082 | 0.769 |

（数值来源：第七轮审计二的 `--scan --split tuning`。⚠️ **本轮实测发现口径不一致**：审计二那张表用的是
**`v1<0.45`**，而生产值是 **`v1 < V1_NONE = 0.35`** —— 在 `none` 网格上逐点复算证实
（`(0.10,0.45)`→0.271/0.615、`(0.15,0.45)`→0.082/0.769，与审计二逐位吻合）。
差异只在"残留暴露"列（负例侧有样本落在 `v1 ∈ [0.35,0.45)`）：**0.271 vs 0.318**（0.10 处）、
**0.082 vs 0.200**（0.15 处）⇒ 旧表**低估风险面**。**实施后以脚本输出的生产口径表为准**。）

现有 `curves["none"]` 是 **5×7 = 35 点稠密网格**，信息**在里面**但被噪声埋住；
而 `curves["strong"]` 服务的是**已于 `7270159` 撤下的 strong 档**（对象不存在）⇒ **语义残留**。

⇒ **把后者替换为「v1 固定 `V1_NONE` 的 6 行干净表」**。

---

## 1. 改前事实（2026-10-01 实测）

| 事实 | 证据 |
|---|---|
| `curves["strong"]` 定义 | `scripts/rag_eval.py:252-257`（判据 `e.v1 > 0 and e.sar >= sar_s`） |
| 它的打印点 | `scripts/rag_eval.py:371-374`，横幅写「仅历史参考，勿用于定阈值」 |
| 现有 none 网格 | `scripts/rag_eval.py:245-250`，`sar_n ∈ (0.06…0.40) × v1_n ∈ (0.35…0.75)`，字段 `(sar_n, v1_n, weak_fp, oa)` |
| 生产判据 | `utils/rag/evidence.py:209` `if sar < SAR_NONE and v1 < V1_NONE: none` |
| 阈值常量 | `utils/rag/evidence.py:83-84` `SAR_NONE=0.06` / `V1_NONE=0.35` |
| 唯一相关测试 | `tests/test_rag_eval.py:271-286`（末尾断言 `len(curves["strong"]) >= 6`） |
| 基线 | `HEAD=99f140d`，工作区干净，`340 passed` |

## 2. 影响面（grep 实测，改前**先查引用面**）

- 代码：仅 `scripts/rag_eval.py`（定义 + 打印）
- 测试：仅 `tests/test_rag_eval.py:271-286`
- 文档：`docs/M1_EVAL_REPORT.md:793`（表格里一行「扫描器 strong 曲线降级为仅历史参考」）
- 其它 100+ 处 `strong` 匹配均为**撤档说明性注释/断言**（解释历史、锁撤档），**不动**
- ⚠️ `scripts/rag_threshold_probe.py` 与 `utils/rag/evidence.py` **不引用本曲线**（前者独立冒烟、后者只引用 `SAR_NONE`/`V1_NONE` 常量）

## 3. 变更清单（3 处同步）

### (1) `scripts/rag_eval.py::scan()`
- **删** `strong_out` 循环（:252-257）
- **加** `sar_none_out`：v1 固定 `ev_mod.V1_NONE`，遍历 `SAR_NONE_SWEEP = (0.06, 0.10, 0.15, 0.20, 0.25, 0.30)`
  - 每点三元组 `(sar_n, neg_exposure, over_abstain)`
  - `neg_exposure = (len(a_irr) - irr_none) / n_irr`（负例未判 none 的比例）
  - `over_abstain = oa_sum / n_rel`
- 返回 `{"none": none_out, "sar_none": sar_none_out}`
- docstring：说明新表用途 + 决策纪律（**先定可接受的 oa，再动 `SAR_NONE`**）+ 与 `none` 网格的关系

### (2) `scripts/rag_eval.py::main()` 打印（:368-374）
- 表头 + 6 行 + `← 当前` 标记 + 一行决策提示
- 打印文本用 **ASCII 标记**（沿用本仓 GBK 控制台铁律）

### (3) `tests/test_rag_eval.py`（:271-286）
- 删 strong 断言，保留 none 断言
- 新增 3 条测试（见 §4 Step 1）

## 4. 步骤（每步 2–5 分钟）

### Step 1 · RED：写失败测试
新增/修改 `tests/test_rag_eval.py`：

1. `test_scan_returns_sar_none_sensitivity_grid`
   - 点数 **== 6**；每点 3 元组
   - 含 `ev_mod.SAR_NONE` 那个点
   - **单调性不变量**：`sar_n` 递增 → `neg_exposure` 不增、`over_abstain` 不减
   - **判别力（防恒等摆设）**：构造两个 judge，使表中数值**真的变化**（防「表恒返回同一行」）
2. `test_sar_none_grid_matches_none_grid_at_v1_none`
   - 交叉一致性：与 `curves["none"]` 在共同点 `(sar_n, V1_NONE)` 上数值**逐位一致**（防两处口径漂移）
3. `test_scan_has_no_strong_curve`
   - `"strong" not in curves`（锁残留清除；旧曲线对象已不存在）

**命令**：`python -m pytest -p no:warnings tests/test_rag_eval.py -q`
**预期**：3 条新测试 **FAIL**（`KeyError: 'sar_none'` / `AssertionError: strong 曲线仍在`），其余通过

### Step 2 · Verify RED
确认失败**原因正确**（不是语法错、不是 fixture 错）：
**命令**：`python -m pytest -p no:warnings tests/test_rag_eval.py -q 2>&1 | Select-String -Pattern "sar_none|strong"`
**预期**：失败信息指向 `'sar_none'` 缺失 / strong 仍在 —— **不是** ImportError / TypeError

### Step 3 · GREEN：实现 `scan()` + `main()`
按 §3(1)(2) 改。**只做让测试变绿的最小改动**。

**命令**：`python -m pytest -p no:warnings tests/test_rag_eval.py -q`
**预期**：全绿

### Step 4 · Verify GREEN（分层）
| # | 命令 | 预期 |
|---|---|---|
| 1 | `python -m pytest -p no:warnings -q` | **343 passed**（340 + 3） |
| 2 | `python scripts/rag_eval.py --split tuning --scan` | 表出 6 行；`0.06` 行标 `← 当前`、`oa=0.000`；`RESULT: OK`；`EXIT=0` |
| 3 | `python scripts/rag_eval.py --split holdout --scan` | 表出 6 行；`RESULT: OK`；`EXIT=0` |
| 4 | `python scripts/rag_threshold_probe.py --holdout` | `RESULT: OK`；`EXIT=0`（K6 不回归） |
| 5 | `$LASTEXITCODE` 检查 | 全部 `0` |

⚠️ **对照检查**：Step 4-2 的 `0.06` 行数值应与 §0 表（审计二给的 tuning 数值 0.459 / 0.000）**一致** —— 不一致则说明口径变了，必须查清再往下走。

### Step 5 · 清语义残留
- `docs/M1_EVAL_REPORT.md:793` 那一行改为指向新表
- 新增 §4j 记录本轮（改动 + 实测表 + 决策提示）
- 新增一行到 `CHANGELOG.md` 的 `[Unreleased]` → `### Changed`
**命令**：`Select-String -Path docs/*.md,tests/golden/rag/*.md -Pattern "仅历史参考|假想|curves\[.strong.\]" -Encoding utf8`
**预期**：**零命中**（旧口径引用清空）

### Step 6 · 独立审计 + 提交
- 跑 critic 子代理审本轮 `git diff`（特别问：新表是否**可被打破**？口径是否与 `evaluate()` 一致？）
- `git add` 本轮文件 → commit（不含文档/CHANGELOG 之外的无关改动）→ push

## 5. 明确不做（YAGNI）

- ❌ **不动 `SAR_NONE` 取值本身** —— 本任务只产出**表**；调不调、调到几，需先由用户定「可接受的引用丢失率」
- ❌ 不实现 LLM 判官（U3-b，另立任务）
- ❌ 不改 `curves["none"]` 网格（v1 维度仍有交叉验证价值）

## 6. 回滚

```bash
git checkout -- scripts/rag_eval.py tests/test_rag_eval.py
```
（本次不涉及数据/索引，无状态迁移）
