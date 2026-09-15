# -*- coding: utf-8 -*-
"""M1 内核验收探针（K2 / K3 专用，见 docs/M1_KERNEL_SPEC.md §6）。

用法：
  python scripts/rag_probe.py                                          # K2：中文长查询命中
  python scripts/rag_probe.py --no-embed                               # K3-a：纯 BM25 单路
  python scripts/rag_probe.py --embed-url http://127.0.0.1:1/api/embed # K3-b：embedding 挂掉自动降级

⚠️ 必须是**脚本文件**，不能用 `python -c`：bash 内联传中文时，Python 按系统 ANSI
（cp936）解析命令行源码，中文查询词会在**输入层**就损坏 —— 2026-09-15 实测因此
让首轮 FTS5 探针产生假阴性（当时误以为 trigram 完全不可用）。
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rag import embed as embed_mod            # noqa: E402
from utils.rag.chunker import chunk_document        # noqa: E402
from utils.rag.hybrid import run_hybrid             # noqa: E402
from utils.rag.tokenize import tokenize             # noqa: E402

# 三篇样例文档（模拟 akshare 公告/研报落盘后的文本）
SAMPLES = [
    "贵州茅台上半年营业收入同比增长百分之十五，净利润增速略低于营收增速。"
    "公司表示直销渠道占比继续提升，i茅台平台贡献显著。",
    "比亚迪新能源汽车七月销量创新高，海外出口同比增长明显，"
    "但单车均价受价格战影响有所下滑。",
    "券商研报指出：白酒行业进入存量竞争阶段，高端酒价格带保持稳定，"
    "次高端库存压力较大，建议关注渠道改革进展。",
]

# 中文长查询（≥5 字）——正是 FTS5 实测失败的那类查询
QUERY = "茅台上半年营收增长多少"


def build_chunks():
    chunks = []
    for i, text in enumerate(SAMPLES):
        for c in chunk_document(text):
            c["doc_index"] = i
            chunks.append(c)
    return chunks


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default=QUERY)
    ap.add_argument("--no-embed", action="store_true", help="跳过 embedding，只跑 BM25 单路")
    ap.add_argument("--embed-url", default=None, help="覆盖 ollama 地址（用于模拟它挂掉）")
    ap.add_argument("--top-n", type=int, default=3)
    args = ap.parse_args(argv)

    if args.embed_url:
        embed_mod.OLLAMA_URL = args.embed_url

    chunks = build_chunks()
    print("[probe] docs={} chunks={}".format(len(SAMPLES), len(chunks)))
    print("[probe] query={!r} tokens={}".format(args.query, tokenize(args.query)))

    matrix, mode = None, "hybrid"
    if not args.no_embed:
        try:
            vecs = embed_mod.embed_texts([c["text"] for c in chunks])
            matrix = np.asarray(vecs, dtype="float32")
            print("[probe] embedding OK dim={}".format(matrix.shape[1]))
        except RuntimeError as e:
            print("[probe] embedding 不可用 -> 自动降级为纯 BM25 单路")
            print("[probe] 原因：{}".format(str(e)[:160]))
            matrix, mode = None, "bm25-only"
    else:
        mode = "bm25-only"
        print("[probe] --no-embed：跳过 embedding")

    kwargs = {}
    query_vec = None
    if matrix is None:
        # 纯 BM25 单路：零向量让向量路自然失效；此时必须把「相关性双判据」也归零，
        # 否则 max_sim=0 会触发 A3 短路，BM25 的结果永远拿不到。
        # ⚠️ 只在**这个降级分支**里关判据 —— 早期版本无条件传 min_sim=0，
        #    导致连「量子计算」这类完全无关的查询都返回 3 条（2026-09-15 实测发现）。
        matrix = np.zeros((len(chunks), embed_mod.DIM), dtype="float32")
        query_vec = np.zeros(embed_mod.DIM, dtype="float32")
        kwargs = {"min_sim": 0.0, "min_sim_ratio": 0.0}

    order, rrf = run_hybrid(
        args.query, matrix, [{"text": c["text"]} for c in chunks],
        k=args.top_n, query_vec=query_vec, **kwargs,
    )

    print("[probe] mode={} hits={}".format(mode, len(order)))
    if not order:
        print("[probe] RESULT: NO_HIT（未找到相关内容 —— 不得凭空补）")
        return 0
    for rank, idx in enumerate(order, 1):
        c = chunks[idx]
        print("[probe] #{} score={:.6f} is_table={} text={}".format(
            rank, rrf[idx], c["is_table"], c["text"][:60]))
    print("[probe] RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
