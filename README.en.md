# Invest Concierge

[![CI](https://github.com/cx-ssg/invest-concierge/actions/workflows/ci.yml/badge.svg)](https://github.com/cx-ssg/invest-concierge/actions/workflows/ci.yml)

> Open-source A-share & fund AI personal investment assistant. Free data, works out of the box.
> 中文版：[README.md](README.md)

## ⚠️ Disclaimer

For **educational and research purposes only**. Not investment advice. Data comes from free public APIs (AkShare etc.) and may be delayed or inaccurate.

## What

A Streamlit-based assistant for China A-share stocks & funds, powered by free data + optional DeepSeek AI:
- **Dual-track navigation**: switch between Fund / Stock columns in the top-right.
- **Stock diagnosis**: fundamentals, financial minefields (8 risks), moat (6 dimensions), valuation, financial reports, plus an AI multi-analyst debate (bull/bear/risk → verdict with rating).
- **Fund tracking**: holdings, P&L, daily valuation, investment diary.

## Quick Start

Python 3.9+:

```bash
git clone https://github.com/cx-ssg/invest-concierge.git
cd invest-concierge
pip install -r requirements.txt
python -m streamlit run app.py
```

Optional AI: copy `local_env.bat.example` to `local_env.bat` and add your DeepSeek API key. Everything works without a key (AI features gracefully show a guide card).

## Screenshots

See `assets/screenshots/` (dashboard, diagnosis, navigation).

## Architecture

`app.py → web_agent.py (router) → pages/ (20) → ui_components/ + utils/ai_helper → data/ (14 modules) → AkShare / SQLite / (optional) DeepSeek API`

## License

MIT — see [LICENSE](LICENSE).

*Not financial advice. Trade at your own risk.*