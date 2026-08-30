# 投资私人管家 · invest-concierge

> A股基金 AI 私人顾问 —— 开源 · 免费数据 · 打开即用

## ⚠️ 免责声明（请先阅读）

- 本项目仅供**学习与技术研究**，不构成任何投资建议或操作依据。
- 股市有风险，入市需谨慎；据此操作，风险自担。
- 项目数据来自公开免费接口（AkShare / 天天基金 / 新浪财经），可能存在延迟或错误，请以官方披露信息为准。

## 这是什么

**投资私人管家**是一个基于 Streamlit 的开源 A股/基金分析助手：不依赖任何收费数据源，克隆下来就能跑。内置 AI 能力（可选接入 DeepSeek），把财报、估值、资金面翻译成普通人能看懂的话。

## ✨ 当前功能

| 功能 | 说明 | 状态 |
|---|---|---|
| 💼 基金持仓管理 | 录入持仓，自动追踪收益与当日实时估值 | ✅ |
| 📊 资产总览 | 总资产与持仓收益一览 | ✅ |
| 📔 投资日记 | 记录每笔操作的理由，与未来的自己对话 | ✅ |
| 💬 AI 对话 | 接入 DeepSeek 的投资问答助手 | ✅ 可选¹ |
| 🩺 股票综合诊断 | 财报 / 排雷 / 护城河 / 估值多引擎体检 + AI 多角色辩论 | 🚧 开发中 |

¹ AI 对话需要配置 DeepSeek API Key（[注册地址](https://platform.deepseek.com/)，有免费额度）；**不配置 Key 时其余功能完整可用**。

## 🚀 快速开始

环境要求：**Python 3.9+**（Windows 安装时建议勾选 Add Python to PATH）

```bash
git clone https://github.com/cx-ssg/invest-concierge.git
cd invest-concierge
pip install -r requirements.txt
```

配置 AI Key（可选）：复制 `local_env.bat.example` 为 `local_env.bat`，填入你的 DeepSeek API Key。

启动：

```bash
# Windows：双击 start.bat，或
python -m streamlit run app.py
```

浏览器会自动打开 http://localhost:8501 。

## 🧭 开发路线

- 📊 基金专栏 / 📈 股票专栏双轨导航
- 🩺 股票综合诊断：基本面 · 财务排雷（8 大雷区）· 护城河评分（6 维）· 估值 · 财报三表
- 🎭 AI 多角色辩论：多头研究员 / 空头风控官 / 决策委员会主席，基于体检数据辩论出结论
- 🌙 每日复盘自动生成、收盘扫描提醒

完整路线图将随 v1.0 一并发布。

## 🛠 技术栈

Python · Streamlit · SQLite · AkShare / 天天基金 / 新浪财经 · DeepSeek API

## 📄 许可证

[MIT](LICENSE)
