# -*- coding: utf-8 -*-
"""
内存缓存装饰器 - 用于缓存 API 请求结果，减少重复请求
"""

import time
import functools

# ==================== 缓存时间常量（秒） ====================
# 实时行情数据：60 秒
CACHE_QUOTE = 60

# 基本面/财务数据：24 小时
CACHE_FUNDAMENTALS = 24 * 3600

# 估值数据：24 小时
CACHE_VALUATION = 24 * 3600

# 龙虎榜数据：1 小时
CACHE_DRAGON = 3600

# 资金流向数据：1 小时
CACHE_MONEYFLOW = 3600

# 涨停复盘数据：1 小时
CACHE_LIMIT_UP = 3600

# 情绪数据：1 小时
CACHE_SENTIMENT = 3600

# 板块/行业数据：12 小时
CACHE_SECTOR = 12 * 3600

# 财务排雷数据：24 小时
CACHE_MINEFIELD = 24 * 3600

# 护城河分析数据：24 小时
CACHE_MOAT = 24 * 3600

# 市场数据：60 秒
CACHE_MARKET = 60


class MemoryCache:
    """内存缓存类"""

    def __init__(self):
        self._cache = {}

    def get(self, key):
        """获取缓存"""
        if key in self._cache:
            item = self._cache[key]
            if time.time() < item['expire_time']:
                return item['value']
            else:
                # 过期删除
                del self._cache[key]
        return None

    def set(self, key, value, ttl):
        """设置缓存"""
        self._cache[key] = {
            'value': value,
            'expire_time': time.time() + ttl
        }

    def clear(self):
        """清空所有缓存"""
        self._cache.clear()

    def clear_by_prefix(self, prefix):
        """按前缀清空缓存"""
        keys_to_delete = [k for k in self._cache if k.startswith(prefix)]
        for k in keys_to_delete:
            del self._cache[k]

    @property
    def size(self):
        """当前缓存条目数"""
        return len(self._cache)


# 全局缓存实例
_cache = MemoryCache()


def cached(ttl, cache_failures=False, failure_ttl=300):
    """缓存装饰器

    用法：
        @cached(CACHE_QUOTE)
        def get_stock_price(code):
            ...

    参数：
        ttl: 缓存时间（秒）
        cache_failures: 是否缓存失败（None）结果——2026-08-31 新增，
            防卡顿：东财不稳时失败也缓存 short TTL（默认 5 分钟），
            避免切页每次都重试 20s；成功结果仍走 ttl
        failure_ttl: 失败缓存的 TTL（默认 300s = 5 分钟）
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            key = "{}:{}:{}".format(func.__name__, str(args), str(sorted(kwargs.items())))
            # 尝试获取缓存
            result = _cache.get(key)
            if result is not None:
                return result
            # 执行函数
            result = func(*args, **kwargs)
            # 存入缓存
            if result is not None:
                _cache.set(key, result, ttl)
            elif cache_failures:
                # 失败也缓存（短 TTL）——防切页重复白等
                _cache.set(key, None, failure_ttl)
            return result
        return wrapper
    return decorator


def clear_cache():
    """清空所有缓存"""
    _cache.clear()


def get_cache_size():
    """获取缓存条目数"""
    return _cache.size
