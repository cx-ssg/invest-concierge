# -*- coding: utf-8 -*-
"""本地 embedding 客户端（Ollama bge-m3，1024 维）。

复用自知识库 `co-planning/scripts/embed_client.py`（2026-09-15 搬入）。
stdlib-only（urllib），零额外依赖；含降级链配置位与维度校验。

超时 300s 的依据（源自知识库实测）：单批 64 块 ≈ 43s（bge-m3 约 1.47 块/秒），
叠加首次模型加载与限频后 60s 不够用；Ollama 真挂时最多多等 5 分钟，离线重建可接受。
"""
import json
import urllib.request

OLLAMA_URL = "http://127.0.0.1:11434/api/embed"
MODEL = "bge-m3"
DIM = 1024
DEFAULT_TIMEOUT = 300

# 降级链占位（与知识库同构）：Ollama 不可用时改用 OpenAI 兼容端点
BACKUP_EMBED_URL = None
BACKUP_EMBED_KEY = None
BACKUP_EMBED_MODEL = "BAAI/bge-m3"


EMBED_BATCH = 32   # 单次请求最大条数（依据见 embed_texts_batched）


def embed_texts_batched(texts, batch_size=EMBED_BATCH, model=MODEL,
                        timeout=DEFAULT_TIMEOUT, on_progress=None):
    """分批 embedding —— **大批量必须走这个，不要直接调 embed_texts**。

    实测依据（2026-09-16）：1041 个公告块一次性 POST → ollama `HTTP Error 400 Bad Request`
    （请求体过大）。知识库侧 `search_wiki.build_index` 一直是分批的（64），本模块初版漏了这层。

    `on_progress(done, total)` 可选回调，供 ingest 打印可观测进度。
    """
    if batch_size <= 0:
        # 静默返回 [] 会让调用方以为「没有向量要算」而丢数据 —— 必须响亮地失败
        raise ValueError("batch_size 必须 >= 1，收到 {}".format(batch_size))
    texts = list(texts or [])
    if not texts:
        return []
    out = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        got = embed_texts(batch, model=model, timeout=timeout)
        if len(got) != len(batch):
            raise RuntimeError(
                "embedding 条数不一致：请求 {} 条、返回 {} 条（错位向量比报错更危险）".format(
                    len(batch), len(got)))
        out.extend(got)
        if on_progress:
            on_progress(min(i + batch_size, len(texts)), len(texts))
    return out


def embed_texts(texts, model=MODEL, timeout=DEFAULT_TIMEOUT):
    """单批 embedding，返回 list[list[float]]。

    空输入直接返回 []（不触发网络）；失败抛 RuntimeError 并给出可操作指引（K3 验收）。
    """
    texts = list(texts or [])
    if not texts:
        return []

    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - 统一转成带指引的 RuntimeError
        if BACKUP_EMBED_URL and BACKUP_EMBED_KEY:
            return _embed_via_backup(texts, model=model, timeout=timeout)
        raise RuntimeError(
            "Ollama embedding 失败（{}）。请确认 Ollama 正在运行且已 pull {}："
            "`ollama pull bge-m3`；或配置降级链 BACKUP_EMBED_URL / BACKUP_EMBED_KEY"
            "（如 SiliconFlow bge-m3）；或改用纯 BM25 单路检索。".format(e, model)
        ) from e

    embs = data.get("embeddings")
    if not embs:
        raise RuntimeError("Ollama 返回空 embeddings：{}".format(str(data)[:200]))
    for e in embs:
        if len(e) != DIM:
            raise RuntimeError(
                "维度不匹配：期望 {}，实际 {}（模型={}）".format(DIM, len(e), model)
            )
    return embs


def embed_one(text, model=MODEL, timeout=DEFAULT_TIMEOUT):
    """单条便捷封装。"""
    vecs = embed_texts([text], model=model, timeout=timeout)
    return vecs[0] if vecs else []


def _embed_via_backup(texts, model=BACKUP_EMBED_MODEL, timeout=DEFAULT_TIMEOUT):
    """OpenAI 兼容备用端点（BASE64 无关，直接 POST /v1/embeddings）。"""
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        BACKUP_EMBED_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer {}".format(BACKUP_EMBED_KEY),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [d["embedding"] for d in data.get("data", [])]
