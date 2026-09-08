# -*- coding: utf-8 -*-
"""
配置文件 - 存放 API 地址、缓存时间等常量
"""

import os
import sys

# ==================== 环境变量 ====================
# 可选加载根目录 .env（复制 .env.example 为 .env 填入 DEEPSEEK_API_KEY 即可生效）。
# load_dotenv 默认不覆盖已存在的系统环境变量（系统变量优先级更高）。
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # 未安装 python-dotenv 时退化为纯系统环境变量模式

# v1.2.1：兼容 local_env.bat（exe 直接启动时 start.bat 链路不存在，.bat 未被 source，
# key 读不到 → 引擎"假未配置"）。这里解析 bat 的 set KEY=value 行注入环境变量——
# 仅本机开发场景文件，不随 exe 分发；优先级仍低于系统环境变量（已存在则跳过）。
def _load_local_env_bat():
    """解析 local_env.bat 的 `set KEY=value` 行（兼容 REM 注释/引号/尾部空白）。

    多路径探测（按优先级）：
    1. 源码目录（__file__ 旁）——开发/start.bat 场景
    2. exe 同目录（sys.executable 旁）——本机双击 exe 测试场景
    3. cwd——便携/任意启动场景
    仅本机开发文件，不随 exe 分发；系统环境变量优先级最高（已存在则跳过）。
    """
    import re
    import sys as _sys
    cands = []
    base = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(base, "local_env.bat"))
    try:
        cands.append(os.path.join(os.path.dirname(os.path.abspath(_sys.executable)), "local_env.bat"))
    except Exception:
        pass
    cands.append(os.path.join(os.getcwd(), "local_env.bat"))
    for bat in cands:
        if not os.path.exists(bat):
            continue
        try:
            with open(bat, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m = re.match(r"\s*set\s+([A-Za-z_][A-Za-z0-9_]*)=(.+?)\s*$", line, re.IGNORECASE)
                    if not m:
                        continue
                    k, v = m.group(1), m.group(2).strip().strip('"')
                    if k not in os.environ and v:  # 系统环境变量优先
                        os.environ[k] = v
        except OSError:
            continue
        break  # 命中第一个存在的 bat 即停

_load_local_env_bat()

# ==================== API 配置 ====================
DEEPSEEK_API_BASE = "https://api.deepseek.com"
# v1.2.1：2026-07-24 起 deepseek-chat/deepseek-reasoner 已停用（调旧名 400/404）。
# 默认模型升级为 deepseek-v4-flash（快+便宜，非思考=旧 chat 行为）；
# reasoner 用同一模型显式开思考（V4 设计：思考/非思考=同 ID 参数切换，非不同模型名）。
# thinking 开关通过 extra_body={"thinking": {"type": "enabled"}} 传递（见 ai_helper.call_llm）。
DEEPSEEK_MODEL = "deepseek-v4-flash"
# reasoner：返回 reasoning_content（模型原生思考流，对话中心思考链展示用）
DEEPSEEK_REASONER_MODEL = os.environ.get("DEEPSEEK_REASONER_MODEL", "deepseek-v4-flash")

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
# v1.1 修复（发版前审查）：数据文件不再落 exe 同目录（相对路径=cwd 随机性，
# 绿色 exe 放哪 db 就散哪、不同启动位置多份数据库）。统一重定向到
# %LOCALAPPDATA%\invest-concierge\（与安装器 MyAppDataDir 一致，iss:19）；
# 源码跑（无 frozen）保持项目根目录不变——开发者工作流零影响。
# 环境变量 INVEST_DATA_DIR 可显式覆盖（测试/便携模式用）。
def _data_dir():
    if os.environ.get("INVEST_DATA_DIR"):
        return os.environ["INVEST_DATA_DIR"]
    if getattr(sys, "frozen", False):  # PyInstaller exe：数据跟用户走
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "invest-concierge")
    else:  # 源码跑：项目根（原有行为）
        d = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(d, exist_ok=True)
    return d


_DATA_DIR = _data_dir()
MY_FUNDS_FILE = os.path.join(_DATA_DIR, "my_funds.json")
DIARY_FILE = os.path.join(_DATA_DIR, "investment_diary.json")
ALERT_SETTINGS_FILE = os.path.join(_DATA_DIR, "alert_settings.json")
DB_FILE = os.path.join(_DATA_DIR, "fund_agent.db")

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
