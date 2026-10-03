# report-F-R1-evidence · F 阶段**决定性文本证据**归档

> 用途：F-R1 整改时发现（审计 F11③）四份 F 阶段报告反复声称 `.f0a/`、`.f0b/`、`.f1/`
> 「已被 `.gitignore` 忽略」——**不成立**：`.gitignore` 无对应规则、工作区里这些目录
> **不存在**，原始证据只存在于 `D:/Vault/Handoff/itt-20261002/*-scratch/`。
> ⇒ 任何人只拿到冻结仓都**无法复现** F 阶段结论。本目录按审计建议，把**小体积、决定性**的
> 文本产物复制进仓库（**未改动一个字节**，sha256 前 16 位见下表）。

## 来源与哈希（只读复制，源目录 `D:/Vault/Handoff/itt-20261002/`）

| 本目录文件 | 源路径 | sha256[:16] |
|---|---|---|
| `stageF-FROZEN.md` | `stageF-FROZEN.md` | `099458212be397e5` |
| `f0a/measure_f0a_rerun.txt` | `f0a-scratch/measure_f0a_rerun.txt` | `94429148402d426f` |
| `f0a/fixture_dup6b.txt` | `f0a-scratch/fixture_dup6b.txt` | `34a1ee694d42cf38` |
| `f0b/arms_tuning.txt` | `f0b-scratch/arms_tuning.txt` | `06e36c4f048461db` |
| `f0b/metrics_tuning.txt` | `f0b-scratch/metrics_tuning.txt` | `a62298b605e9fa73` |
| `f0b/holdout_final.txt` | `f0b-scratch/holdout_final.txt` | `dbb6f3052349cdb2` |
| `f0b/fixture_final2.txt` | `f0b-scratch/fixture_final2.txt` | `ba289c102d6976ee` |
| `f0b/invariance.txt` | `f0b-scratch/invariance.txt` | `e9b819ed7395c784` |
| `f0b/sweep_tuning.txt` | `f0b-scratch/sweep_tuning.txt` | `de01f368f0d60388` |
| `f0b/tuning_scan.txt` | `f0b-scratch/tuning_scan.txt` | `f5b67a4809016d16` |
| `f1/FINAL_tuning.txt` | `f1-scratch/FINAL_tuning.txt` | `0955a827f058c40e` |
| `f1/holdout_prod_run1..5.txt` | `f1-scratch/holdout_prod_run{1..5}.txt` | `1beea86830e4a367` / `25d001c95bb76ece` / `e0cadc1eeeb8994a` / `ed5506805da49d9c` / `ed430fea7fb24d99` |

## 未归档（体积原因，仍只在源目录）

- `f1-scratch/raw_tuning_*.json` / `raw_holdout_*.json`（每份 ~700KB，共 ~100 份）、
  `f1-scratch/trigger_map.json`（1.4MB）、`f0a-scratch/measure_f0a.json`（71KB）、
  `stageF-codex.out`（1.58MB）、`stageF-claude.out`（16KB）等。
- 它们的**汇总数字**都在上表文本里，且 `report-F-R1.md` 的每条结论都标了可复现命令。

## F-R1 新增证据（不在本目录，属本轮产物）

`.fr1/`（工作区，未跟踪）：`fr1_offline_evidence.py` 及其 `fr1_fr2_prod_leak.txt` /
`fr1_fr3_ablation.txt` / `fr1_fr5_judge_window.txt` / `fr1_fr7_scope.txt` / `fr1_fr9_gold.txt`、
`fr1_fr4_s009.txt`、`fr1_fr6_real_chain.txt`、`judge_after_run{1..5}.txt`、`fixture_after_edits.txt`。
本目录的 `D:/Vault` 源**不可写**（沙箱 workspace-write）⇒ 涉及 `report-F0b.md` / `report-F1.md`
的订正以 **errata** 形式写在 `report-F-R1.md`（含逐条替换文本），未回写 Vault 原文。
