# -*- coding: utf-8 -*-
"""
配置文件 - 存放 API 地址、缓存时间等常量
"""

import os

# ==================== 环境变量 ====================
# 可选加载根目录 .env（复制 .env.example 为 .env 填入 DEEPSEEK_API_KEY 即可生效）。
# load_dotenv 默认不覆盖已存在的系统环境变量（系统变量优先级更高）。
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # 未安装 python-dotenv 时退化为纯系统环境变量模式

# ==================== API 配置 ====================
DEEPSEEK_API_BASE = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
# reasoner：返回 reasoning_content（模型原生思考流，对话中心思考链展示用）
DEEPSEEK_REASONER_MODEL = os.environ.get("DEEPSEEK_REASONER_MODEL", "deepseek-reasoner")

# ==================== 缓存配置 ====================
CACHE_TTL = {
    'fund_info': 60,        # 基金基本信息缓存 60 秒
    'fund_history': 300,    # 基金历史数据缓存 5 分钟
    'market_index': 60,     # 大盘指数缓存 60 秒
    'valuation': 86400,     # 估值数据缓存 24 小时
    'hot_sectors': 43200,   # 热门板块缓存 12 小时
}
# 注意：各 data 模块的缓存时间常量定义在 data/cache.py 中
# 本字典仅用于部分页面直接引用，实际缓存策略以 data/cache.py 为准

# ==================== 文件路径 ====================
MY_FUNDS_FILE = "my_funds.json"
DIARY_FILE = "investment_diary.json"
ALERT_SETTINGS_FILE = "alert_settings.json"
DB_FILE = "fund_agent.db"

# ==================== API Key ====================
def get_api_key():
    """获取 DeepSeek API Key

    优先级：系统环境变量 > 根目录 .env（load_dotenv 已并入环境变量）>
    旧式 local_env.bat 兜底（bat 里 `set DEEPSEEK_API_KEY=...` 行）。
    local_env.bat 用 Python 读而非 cmd call——bat 若含 UTF-8 中文注释，
    cmd 以 GBK 解析会报"'xxx' 不是内部或外部命令"（2026-09-04 实测）。
    """
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    bat = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_env.bat")
    if os.path.exists(bat):
        try:
            with open(bat, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.lower().startswith("set deepseek_api_key="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    return ""

API_KEY = get_api_key()
