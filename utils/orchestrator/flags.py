# -*- coding: utf-8 -*-
"""M3 编排层开关：`ORCHESTRATOR=legacy|graph`。

设计依据 `docs/COVERAGE_DESIGN.md` §5.2-1「feature flag 并存：出问题一键切回，**不破坏现有可用版本**」。
"""
import os

MODE_LEGACY = "legacy"
MODE_GRAPH = "graph"
ENV_KEY = "ORCHESTRATOR"
DEFAULT_MODE = MODE_LEGACY


def orchestrator_mode(env=None):
    """返回当前编排模式（`legacy` | `graph`）。

    - 未设置 ⇒ `legacy`（**默认不改变现有行为**）
    - 非法值 ⇒ 回落 `legacy`（**不抛异常**：一个拼错的 env 变量不该把服务打挂，
      但调用方可通过 `orchestrator_mode_source()` 判断是否发生过回落并告警）
    """
    raw = env if env is not None else os.environ.get(ENV_KEY)
    if raw is None or str(raw).strip() == "":
        return DEFAULT_MODE
    v = str(raw).strip().lower()
    return v if v in (MODE_LEGACY, MODE_GRAPH) else DEFAULT_MODE


def orchestrator_mode_source(env=None):
    """返回 `(mode, fell_back)` —— `fell_back=True` 表示 env 设了非法值已回落。"""
    raw = env if env is not None else os.environ.get(ENV_KEY)
    if raw is None or str(raw).strip() == "":
        return DEFAULT_MODE, False
    v = str(raw).strip().lower()
    if v in (MODE_LEGACY, MODE_GRAPH):
        return v, False
    return DEFAULT_MODE, True


def use_graph(env=None):
    """是否走图编排（`True` 当且仅当 `ORCHESTRATOR=graph`）。"""
    return orchestrator_mode(env) == MODE_GRAPH
