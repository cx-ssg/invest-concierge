# -*- coding: utf-8 -*-
"""embedding 客户端回归锁（M1 内核 · utils/rag/embed.py）。

重点：**分批**。2026-09-16 实测：1041 个块一次性 POST 到 ollama → `HTTP Error 400 Bad Request`
（知识库那侧的 `search_wiki.build_index` 是分批 64 的，本模块初版漏了这一层）。
"""
import pytest

from utils.rag import embed as embed_mod


def test_embed_texts_empty_input_returns_empty_list():
    """空输入不得触发网络调用。"""
    assert embed_mod.embed_texts([]) == []


def test_embed_texts_batched_splits_requests(monkeypatch):
    """100 条 → 按 batch_size 拆成多次请求，且顺序与数量都不丢。"""
    calls = []

    def fake_embed(batch, model=embed_mod.MODEL, timeout=embed_mod.DEFAULT_TIMEOUT):
        calls.append(len(batch))
        return [[0.0] * 4 for _ in batch]

    monkeypatch.setattr(embed_mod, "embed_texts", fake_embed)
    vecs = embed_mod.embed_texts_batched([str(i) for i in range(100)], batch_size=32)
    assert len(vecs) == 100, "返回条数必须与输入一致"
    assert calls == [32, 32, 32, 4], "必须按 batch_size 分批：实际 %s" % calls


def test_embed_texts_batched_single_request_when_small(monkeypatch):
    """小于一批时不额外拆分。"""
    calls = []
    monkeypatch.setattr(
        embed_mod, "embed_texts",
        lambda batch, **kw: (calls.append(len(batch)), [[0.0] * 4 for _ in batch])[1],
    )
    embed_mod.embed_texts_batched(["a", "b"], batch_size=32)
    assert calls == [2]


def test_embed_texts_batched_empty_returns_empty():
    assert embed_mod.embed_texts_batched([]) == []


def test_embed_texts_batched_progress_callback(monkeypatch):
    """进度回调必须报告 (已完成, 总数)，供 ingest 打印可观测进度。"""
    monkeypatch.setattr(embed_mod, "embed_texts",
                        lambda batch, **kw: [[0.0] * 4 for _ in batch])
    seen = []
    embed_mod.embed_texts_batched([str(i) for i in range(10)], batch_size=4,
                                  on_progress=lambda done, total: seen.append((done, total)))
    assert seen == [(4, 10), (8, 10), (10, 10)]


def test_embed_failure_raises_with_guidance(monkeypatch):
    """ollama 不可达 → RuntimeError 且消息含 ollama 指引，不得静默返回空向量。"""
    def _boom(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(embed_mod.urllib.request, "urlopen", _boom)
    with pytest.raises(RuntimeError) as ei:
        embed_mod.embed_texts(["x"])
    assert "ollama" in str(ei.value).lower()


def test_embed_texts_batched_rejects_non_positive_batch_size():
    """batch_size <= 0 必须**显式报错**，不得静默返回空。

    旧实现 `range(0, n, -1)` 在 batch_size=-1 时为空 → 静默返回 `[]`（**所有向量丢失**
    而调用方毫无察觉）；batch_size=0 抛的 ValueError 也不带上下文。
    critic 独立审计 2026-09-16 指出（F2）。
    """
    for bad in (0, -1):
        with pytest.raises(ValueError):
            embed_mod.embed_texts_batched(["a", "b"], batch_size=bad)


def test_embed_texts_batched_result_length_mismatch_raises(monkeypatch):
    """条数不一致必须报错 —— 静默交付错位向量比报错危险得多。"""
    def short_embed(batch, **kw):
        return [[0.0] * 4 for _ in batch][:-1]      # 故意少返回一条

    monkeypatch.setattr(embed_mod, "embed_texts", short_embed)
    with pytest.raises(RuntimeError):
        embed_mod.embed_texts_batched(["a", "b"])
