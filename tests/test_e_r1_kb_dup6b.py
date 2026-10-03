# -*- coding: utf-8 -*-
"""`scripts/e_r1_kb_dup6b.py` 的**记录值校验闸门**（F-R1 · 审计 F11①）。

夹具的三条**不变量**（结构/零新内容、纯复制 Δ(A3a)=0、真实翻转 0 条被复现）
始终强制校验；但「记录值校验（strict）」在**判据不匹配任何记录臂**时**无法执行**
—— 旧实现只打一行 `[WARN]` 就静默关掉 strict（退出码仍 0），
「改了判据却拿旧常数验自己」这条路的报警只活在 stdout 里。

本文件把新的**硬失败**语义钉住（`resolve_strict` 是为此抽出的可测单点）。
"""
from scripts import e_r1_kb_dup6b as fx
from utils.rag import evidence as ev_mod


def test_pick_arm_auto_matches_current_predicate():
    """当前生产判据 (0.02, 0.075, 0.45) 必须匹配到 F0b 记录臂。"""
    name, expect, matched = fx.pick_arm("auto")
    assert matched is True
    assert name.startswith("F0b")
    assert expect["control_a3a"] == (50, 51)


def test_pick_arm_auto_unmatched_when_predicate_changes(monkeypatch):
    """判据三元组一变，`auto` 立即失配 —— 这正是必须硬失败的情形。"""
    monkeypatch.setattr(ev_mod, "FEATURE_DF_FRACTION", 0.031)
    _name, _expect, matched = fx.pick_arm("auto")
    assert matched is False


def test_resolve_strict_hard_fails_on_unmatched_predicate():
    """失配 ⇒ `None`（= 硬失败）；只有**显式** `--no-strict` 才允许继续。"""
    assert fx.resolve_strict(None, fx.PRE_E1_SHA1, True) is True
    assert fx.resolve_strict(None, "deadbeef", True) is False
    assert fx.resolve_strict(None, fx.PRE_E1_SHA1, False) is None
    assert fx.resolve_strict(True, fx.PRE_E1_SHA1, False) is None
    assert fx.resolve_strict(False, fx.PRE_E1_SHA1, False) is False
