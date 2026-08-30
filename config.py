# -*- coding: utf-8 -*-
"""
配置文件 - 存放 API 地址、缓存时间等常量
"""

import os

# ==================== API 配置 ====================
DEEPSEEK_API_BASE = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

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
    """获取 DeepSeek API Key"""
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()

API_KEY = get_api_key()
