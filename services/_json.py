# -*- coding: utf-8 -*-
"""
JSON 清洗层（M0 #9）：一切 API 响应过 to_jsonable 再序列化。

处理 FastAPI/pydantic 不认的非 JSON 类型：
    - DataFrame → records 列表
    - Series → 列表
    - NaN / ±inf → None
    - Timestamp / datetime / date → ISO 字符串
    - numpy 标量/数组 → Python 原生
    - set → 排序列表（稳定输出）
递归穿透 dict/list；未知对象 str() 兜底（诊断 payload 的杂项字段不至于炸序列化）。
"""

import math
import datetime

try:  # pandas 可选（诊断/行情返回 DataFrame）
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None

try:  # numpy 标量转换
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def to_jsonable(x, _depth=0):
    """递归转换为 JSON 可序列化结构。_depth 防 self-reference 死循环。"""
    if _depth > 12:
        return str(x)

    # None / bool / str 直通
    if x is None or isinstance(x, (bool, str)):
        return x

    # 数值：NaN / ±inf → None（FastAPI 默认序列化会产出非法 JSON）
    if _is_number(x):
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return None
        return x

    if np is not None:
        # numpy 标量
        if isinstance(x, np.generic):
            try:
                v = x.item()
                if _is_number(v):
                    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                        return None
                return v
            except (ValueError, TypeError):
                return str(x)
        # numpy 数组
        if isinstance(x, np.ndarray):
            return to_jsonable(x.tolist(), _depth + 1)

    if pd is not None:
        if isinstance(x, pd.DataFrame):
            return to_jsonable(x.to_dict(orient="records"), _depth + 1)
        if isinstance(x, pd.Series):
            return to_jsonable(x.tolist(), _depth + 1)
        if isinstance(x, (pd.Timestamp,)):
            return str(x.isoformat() if hasattr(x, "isoformat") else x)
        if isinstance(x, pd.Categorical):
            return to_jsonable(list(x), _depth + 1)

    # 时间类型 → ISO
    if isinstance(x, (datetime.datetime, datetime.date, datetime.time)):
        return x.isoformat()
    if isinstance(x, datetime.timedelta):
        return x.total_seconds()

    # 容器递归
    if isinstance(x, dict):
        return {
            str(k): to_jsonable(v, _depth + 1)
            for k, v in x.items()
        }
    if isinstance(x, (list, tuple)):
        return [to_jsonable(v, _depth + 1) for v in x]
    if isinstance(x, set):
        # set 无序：排序稳定化（不可排序成员回退 str 排序）
        try:
            ordered = sorted(x)
        except TypeError:
            ordered = sorted(x, key=str)
        return [to_jsonable(v, _depth + 1) for v in ordered]

    # bytes → utf-8 尝试
    if isinstance(x, (bytes, bytearray)):
        try:
            return x.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return str(x)

    # 兜底：str()（防诊断 payload 里的杂项对象炸序列化）
    return str(x)
