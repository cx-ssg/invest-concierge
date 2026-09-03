# 后端评审任务书（Codex + Claude Code 双视角）

> 2026-09-03 · 评审对象：invest-concierge 后端（Python 数据层）
> 目的：找 bug / 性能优化点 / 前端对接接口设计 / 后续更新维护
> 背景：前端将重写为 React（FastAPI 桥接），后端是数据+Agent 核心，需为对接做准备

---

## 0. 评审范围（后端代码，16 个数据模块 + 工具层）

```
D:/work/python1/fund_agent/data/        ← 16 个数据模块（A股/基金接口封装）
D:/work/python1/fund_agent/utils/       ← ai_helper/agent_core/agent_memory/common
D:/work/python1/fund_agent/app.py       ← 入口
D:/work/python1/fund_agent/web_agent.py ← Streamlit 路由（前端将废弃，但接口面要抽离）
```

**最近关键变更（commit 52d0a06）**：失败缓存 + 腾讯 fallback + 缩短重试（弱网切页 20s→0s）

## 1. 评审四问

1. **【Bug】** 后端代码有哪些 bug / 隐患 / 边界问题？（按严重度排序）
   - 重点：并发安全（Streamlit rerun/SQLite）、缓存一致性、异常处理、fallback 链漏洞
2. **【性能】** 有哪些可优化点？（按收益排序）
   - 重点：数据拉取慢（东财被墙时的降级路径）、缓存策略、重复请求、Agent 调用链
3. **【前端对接】** 前端将重写为 React（FastAPI 桥），后端需要哪些改造才能好对接？
   - 重点：现有 Streamlit 页面的逻辑如何抽成"纯 API 接口"？哪些函数是 UI 耦合的（st.* 依赖）？
   - 提示：用 grep 找 `import streamlit` / `st.` 在 data/ 和 utils/ 里的泄漏（后端必须零 UI 依赖）
4. **【后续维护】** 代码结构上有什么阻碍长期维护/扩展的点？建议怎么改？

## 2. 输出格式

- Markdown，四节对应四问
- 每节 3-8 条，具体到文件:函数（如 `data/market_api.py:get_market_index`）
- 控制在 1200 字内
- 只读评审，**不要修改任何代码**

## 3. 关键背景

- 技术栈：Python 3.10+，Streamlit（将要换 React），akshare（A股数据，东财被墙时不可达）
- Agent：utils/agent_core.py（11 工具 + 记忆 + 追问链，已完成）
- 测试：pytest 93 个（tests/ 下）
- 数据源现状：东财✗ / 新浪403 / 腾讯✅（实测）

## 3.5 已发现的前端对接障碍（Reasonix 预检，请评审验证/给解法）

**后端 4 个模块 import 了 streamlit（UI 耦合，React 对接的最大硬伤）**：
```
data/market_api.py:7     import streamlit as st   ← 页面顶部必拉大盘，缓存用 st.cache_data
data/database.py:406     import streamlit as st   ← SQLite 持久化也带 UI 依赖
data/fund_api.py:288     import streamlit as st   ← 基金数据
utils/ai_helper.py:8     import streamlit as st   ← Agent 也用（st.session_state?）
```
**请评审**：①这些 st 用法具体是什么（cache_data? session_state?），②改成纯 Python（FastAPI 兼容）的最小方案，③哪些是 DeepSeek 文档里真正需要 st 的（还是纯技术债）。