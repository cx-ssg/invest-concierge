# 版本冻结（stageF）

- 冻结时间：2026-10-03 18:56:05
- 仓库 HEAD：`d723b63120d29561199ab0e684d5ceeb2c92888b`（short `d723b63`）
- 工作区未提交改动（`git status --porcelain`）：
```
(clean)
```
- `git diff --stat HEAD`：
```
(empty)
```

## 关键文件 sha256（前 16 位）

| 文件 | sha256[:16] | 字节 |
|---|---|---|
| `utils/agent_core.py` | `aabba6da335fee23` | 46089 |
| `utils/rag/retrieve.py` | `776d0cab59a39336` | 12936 |
| `utils/rag/evidence.py` | `2e5403d128089ad1` | 21447 |
| `services/agent_service.py` | `23d6aa5937c103e6` | 10217 |
| `frontend/src/components/engine/MarkdownContent.tsx` | `70780f89c3be3192` | 4217 |
| `frontend/src/features/agent/useAgentRun.ts` | `6e3ab2d064c5ce7f` | 7670 |
| `frontend/src/features/agent/ChatArea.tsx` | `9eaa254f1ec1df2d` | 21856 |
| `frontend/src/types/api.ts` | `37db749c5614e2f7` | 14978 |
| `frontend/src/pages/SettingsPage.tsx` | `7c382afd5bb28881` | 15066 |
| `frontend/src/features/memory/MemorySection.tsx` | `eecadb0accc16866` | 20423 |
| `tests/test_m2_memory_api.py` | `3b69ade7d4982c42` | 10287 |
| `README.md` | `042ae69478c5890e` | 23256 |
| `README.en.md` | `c3db0ddbfb8f6547` | 24069 |

> ⚠️ 审计期间**不得再改动工作区**；若需整改，请先出报告（否则审计对象漂移、本轮审计作废）。