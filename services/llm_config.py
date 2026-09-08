# -*- coding: utf-8 -*-
"""
LLM 多 provider 配置服务（v1.2 模型接入）：
- provider 预设注册表（OpenAI 兼容端点）：DeepSeek / SiliconFlow / DashScope / 自定义
- 配置解析优先级：SQLite app_settings（设置页填的）> .env / 环境变量（config.py 现状）
- Key 只落本地 SQLite（%LOCALAPPDATA%\invest-concierge\fund_agent.db），不入仓库
- 回显永远掩码（sk-ab****xy），明文不返回前端

安全红线（M0 §0 延续）：db 里的 key 等价于 .env 的 key——都是本机明文，
不上传、不打日志、不入 git。测试连接时才把明文发往用户填的端点。
"""

# ==================== provider 预设 ====================
# 每项：label 展示名 / base_url 默认端点 / models 常用模型建议 / key_hint 申请地址
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com",
        # 2026-07-24 起 deepseek-chat/deepseek-reasoner 已停用（调旧名 400/404），
        # 现役三模型：flash（默认，快+便宜）/ pro（旗舰推理）/ flash-vision-exp（图片输入）
        # 思考/非思考 = 同一模型 ID 用 thinking 参数切换（V4 设计，非选不同模型名）
        "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"],
        "default_model": "deepseek-v4-flash",
        "key_hint": "platform.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "siliconflow": {
        "label": "SiliconFlow 硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["deepseek-ai/DeepSeek-V4-Flash", "deepseek-ai/DeepSeek-V4-Pro", "Qwen/Qwen3.8-Max"],
        "default_model": "deepseek-ai/DeepSeek-V4-Flash",
        "key_hint": "cloud.siliconflow.cn",
        "env_key": "SILICONFLOW_API_KEY",
    },
    "dashscope": {
        "label": "阿里云百炼 DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3.8-max", "qwen-turbo", "deepseek-v4-flash"],
        "default_model": "qwen3.8-max",
        "key_hint": "bailian.console.aliyun.com",
        "env_key": "DASHSCOPE_API_KEY",
    },
    "custom": {
        "label": "自定义 OpenAI 兼容",
        "base_url": "",
        "models": [],
        "default_model": "",
        "key_hint": "填中转/网关的 base_url",
        "env_key": "CUSTOM_LLM_API_KEY",
    },
}

# ==================== app_settings 键名 ====================
SET_PROVIDER = "llm_provider"
SET_API_KEY = "llm_api_key"
SET_BASE_URL = "llm_base_url"
SET_MODEL = "llm_model"
SET_REASONER_MODEL = "llm_reasoner_model"

# 测试钩子：非 None 时优先于 DB/.env 链（tests patch 此值控制有/无 key 场景，
# 替代旧 patch ai_helper.API_KEY / report_service.API_KEY 的值绑定 patch）
_TEST_KEY_OVERRIDE = None


def _db_get(key, default=""):
    try:
        from data.database import get_setting
        return str(get_setting(key, default) or default)
    except Exception:  # noqa: BLE001 - db 不可用退化到 .env 链路
        return default


def _db_set(key, value):
    from data.database import set_setting
    return bool(set_setting(key, value))


def get_llm_config():
    """解析生效配置：测试钩子 > DB（设置页）> .env/环境变量（config.py 链路）。

    返回 {provider, api_key, base_url, model, reasoner_model, source}
    source: "test" | "settings"（DB）| "env"（.env/环境变量）| "none"
    """
    from config import API_KEY, DEEPSEEK_API_BASE, DEEPSEEK_MODEL, DEEPSEEK_REASONER_MODEL

    provider = _db_get(SET_PROVIDER, "")
    key = _db_get(SET_API_KEY, "")
    base_url = _db_get(SET_BASE_URL, "")
    model = _db_get(SET_MODEL, "")
    reasoner = _db_get(SET_REASONER_MODEL, "")

    if provider and key:
        p = PROVIDERS.get(provider, PROVIDERS["custom"])
        return {
            "provider": provider,
            "api_key": key,
            "base_url": base_url or p["base_url"],
            "model": model or p["default_model"],
            "reasoner_model": reasoner or model,
            "source": "settings",
        }
    # 回落：config.py 链路（.env / 环境变量 → DeepSeek）
    # 测试钩子在此层生效（None=未启用，""=强制无 key）——不屏蔽 DB 链，
    # DB 设置页保存的 key 在钩子="" 时仍然生效（test_llm_save_* 依赖此语义）
    env_key = _TEST_KEY_OVERRIDE if _TEST_KEY_OVERRIDE is not None else API_KEY
    if env_key:
        return {
            "provider": "deepseek",
            "api_key": env_key,
            "base_url": DEEPSEEK_API_BASE,
            "model": DEEPSEEK_MODEL,
            "reasoner_model": DEEPSEEK_REASONER_MODEL,
            "source": "env" if _TEST_KEY_OVERRIDE is None else "test",
        }
    return {"provider": "deepseek", "api_key": "", "base_url": DEEPSEEK_API_BASE,
            "model": DEEPSEEK_MODEL, "reasoner_model": DEEPSEEK_REASONER_MODEL, "source": "none"}


def mask_key(key):
    """sk-abcd****wxyz——只露前 5 后 4"""
    if not key:
        return ""
    if len(key) <= 12:
        return key[:2] + "****"
    return key[:5] + "****" + key[-4:]


def get_settings_view():
    """给前端的安全视图：key 掩码，明文不出服务"""
    cfg = get_llm_config()
    provider = cfg["provider"]
    p = PROVIDERS.get(provider, PROVIDERS["custom"])
    return {
        "ok": True,
        "provider": provider,
        "provider_label": p["label"],
        "api_key_masked": mask_key(cfg["api_key"]),
        "api_key_configured": bool(cfg["api_key"]),
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "reasoner_model": cfg["reasoner_model"],
        "source": cfg["source"],
        "custom_base_url": _db_get(SET_BASE_URL, "") if provider == "custom" else "",
    }


def save_llm_config(provider, api_key="", base_url="", model="", reasoner_model=""):
    """保存设置页配置。api_key 为空 = 沿用已有（不覆盖）；"-" = 清除。"""
    if provider not in PROVIDERS:
        return {"ok": False, "error": f"未知 provider: {provider}"}
    p = PROVIDERS[provider]
    _db_set(SET_PROVIDER, provider)
    if api_key == "-":
        _db_set(SET_API_KEY, "")
    elif api_key:
        _db_set(SET_API_KEY, api_key.strip())
    if provider == "custom":
        _db_set(SET_BASE_URL, (base_url or "").strip().rstrip("/"))
        _db_set(SET_MODEL, (model or "").strip())
    else:
        _db_set(SET_BASE_URL, "")  # 预设 provider 用注册表端点
        _db_set(SET_MODEL, (model or "").strip())
    _db_set(SET_REASONER_MODEL, (reasoner_model or "").strip())
    return {"ok": True, **get_settings_view()}


def test_connection(provider, api_key="", base_url="", model=""):
    """用给定参数（未保存也可）发一次最小请求验证连通。"""
    import time
    from openai import OpenAI

    p = PROVIDERS.get(provider)
    if not p:
        return {"ok": False, "error": f"未知 provider: {provider}"}
    key = api_key.strip() or _db_get(SET_API_KEY, "")
    url = (base_url.strip().rstrip("/") if provider == "custom" else "") or p["base_url"]
    mdl = (model.strip() if provider == "custom" else "") or model.strip() or p["default_model"]
    if not key:
        return {"ok": False, "error": "缺少 API Key"}
    if provider == "custom" and not url:
        return {"ok": False, "error": "自定义 provider 需要填 Base URL"}
    t0 = time.time()
    try:
        client = OpenAI(api_key=key, base_url=url, timeout=15, max_retries=0)
        resp = client.chat.completions.create(
            model=mdl,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=8,
        )
        ms = int((time.time() - t0) * 1000)
        content = (resp.choices[0].message.content or "").strip()
        return {"ok": True, "latency_ms": ms, "model": mdl,
                "reply": (content[:40] + "…") if len(content) > 40 else content}
    except Exception as e:  # noqa: BLE001 - 错误信息透传给用户
        msg = str(e)
        # 常见错误翻译
        if "401" in msg or "Unauthorized" in msg or "Invalid API key" in msg:
            hint = "Key 无效或未授权（401）"
        elif "404" in msg or "Not Found" in msg:
            hint = "端点或模型名不对（404）——检查 Base URL / 模型 ID"
        elif "429" in msg or "rate" in msg.lower():
            hint = "限流/额度不足（429）"
        elif "timeout" in msg.lower() or "timed out" in msg.lower():
            hint = "连接超时（15s）——检查网络/代理"
        else:
            hint = msg[:160]
        return {"ok": False, "error": hint, "latency_ms": int((time.time() - t0) * 1000)}
