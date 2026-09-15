# -*- coding: utf-8 -*-
"""在线评测脚本（P0-3-B）—— 真调模型跑 golden set

用法：
    python scripts/eval_agent.py                     # dry-run（默认）：只加载用例 + 校验工具名，不花钱
    python scripts/eval_agent.py --run               # 真跑（会调用模型，产生费用）
    python scripts/eval_agent.py --run --limit 5     # 只跑前 5 条
    python scripts/eval_agent.py --run --ids fund_info_001,market_index_008
    python scripts/eval_agent.py --run --json out.json
    python scripts/eval_agent.py --run --price 0.5,1.5   # 可选：显式给「输入,输出」单价（元/百万 token）

判定约定（与 `tests/golden/cases.py` 头部一致）：
- **工具命中**：按**集合**判定（顺序不敏感，模型可能先估值再诊断）；
  仅当用例显式标 `order_sensitive: True` 时才按序列严格比对。
- **事实命中**：`expect_facts` 里每个词是否出现在最终回答中（宽松子串匹配，仅作参考）。
- **不默认报钱**：单价会变（且不同 provider 不同），需要时用 `--price` 显式给；默认只报 token。
- 与离线测试的分工：离线（`tests/test_golden_offline.py`）锁**编排契约**；本脚本才测
  「模型选得对不对」——这才是真正的**评测**。
"""
import argparse
import importlib.util
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_GOLDEN = os.path.join(ROOT, "tests", "golden", "cases.py")


def load_cases():
    """按路径加载 golden set（tests/ 下没有 __init__.py，走包导入会踩同名 tests 包）"""
    spec = importlib.util.spec_from_file_location("golden_cases", _GOLDEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CASES


def load_result():
    from utils.agent_core import agent_run, TOOL_REGISTRY  # noqa: F401
    return agent_run


def current_provider_info():
    try:
        from services.llm_config import get_llm_config
        cfg = get_llm_config()
        return cfg.get("provider", "?"), cfg.get("reasoner_model") or cfg.get("model") or "?", bool(cfg.get("api_key"))
    except Exception as e:  # noqa: BLE001
        return "?", "?", False if "?" in str(e) else False


def evaluate_one(case, agent_run, timeout_hint=""):
    """跑一条用例，返回结果 dict（不抛异常）"""
    t0 = time.time()
    try:
        r = agent_run(case["question"], max_tool_rounds=8)
    except Exception as e:  # noqa: BLE001
        return {"id": case["id"], "ok": False, "error": "{}: {}".format(type(e).__name__, e),
                "elapsed": round(time.time() - t0, 2)}
    elapsed = round(time.time() - t0, 2)

    actual = [t["name"] for t in r.get("tool_trace", [])]
    expect = list(case["expect_tools"])
    if case.get("order_sensitive"):
        tools_hit = actual == expect
    else:
        tools_hit = set(expect).issubset(set(actual))
    missing = [n for n in expect if n not in actual]
    extra = [n for n in actual if n not in expect]

    answer = r.get("content") or ""
    facts = case.get("expect_facts") or []
    facts_hit = [f for f in facts if f in answer]
    usage = r.get("usage") or {}

    return {
        "id": case["id"], "ok": tools_hit, "elapsed": elapsed,
        "expect_tools": expect, "actual_tools": actual,
        "missing": missing, "extra": extra,
        "facts": facts, "facts_hit": facts_hit,
        "usage": usage,
        "answer_preview": answer[:120].replace("\n", " "),
    }


def main():
    ap = argparse.ArgumentParser(description="在线评测（真调模型）")
    ap.add_argument("--run", action="store_true", help="真跑（默认只做 dry-run 检查，不花钱）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条")
    ap.add_argument("--ids", default="", help="逗号分隔的用例 id")
    ap.add_argument("--json", default="", help="把结果写到该 json 文件")
    ap.add_argument("--price", default="", help="可选：输入,输出 单价（元/百万 token），给了才报钱")
    args = ap.parse_args()

    cases = load_cases()
    from utils.agent_core import TOOL_REGISTRY
    bad = [(c["id"], n) for c in cases for n in c["expect_tools"] if n not in TOOL_REGISTRY]
    if bad:
        print("❌ 用例引用了不存在的工具（先修 golden set）：%s" % bad)
        return 2

    if args.ids:
        want = {s.strip() for s in args.ids.split(",") if s.strip()}
        cases = [c for c in cases if c["id"] in want]
    if args.limit:
        cases = cases[: args.limit]

    provider, model, has_key = current_provider_info()
    print("=" * 68)
    print("golden set: %d 条 ｜ provider=%s ｜ model=%s" % (len(cases), provider, model))
    print("工具名校验: ✅ 全部存在于注册表（%d 个）" % len(TOOL_REGISTRY))
    print("=" * 68)

    if not args.run:
        print("【dry-run】未加 --run，不调用模型。计划执行的用例：")
        for c in cases:
            print("  - %-22s %s" % (c["id"], c["question"]))
        print("\n真跑请加 --run（会真实调用模型并产生费用）。")
        return 0

    if not has_key:
        print("❌ 未配置 API Key（设置页或 .env），无法在线评测。")
        return 2

    print("⚠️  将真实调用模型 %d 次，可能产生费用。开始…\n" % len(cases))
    agent_run = load_result()

    results = []
    t_all = time.time()
    for i, case in enumerate(cases, 1):
        res = evaluate_one(case, agent_run)
        results.append(res)
        mark = "✅" if res.get("ok") else "❌"
        print("%s [%d/%d] %-22s %5.1fs  期望=%s 实际=%s"
              % (mark, i, len(cases), case["id"], res.get("elapsed", 0),
                 res.get("expect_tools"), res.get("actual_tools")))
        if not res.get("ok") and res.get("missing"):
            print("     缺: %s%s" % (res["missing"], "  多: %s" % res["extra"] if res.get("extra") else ""))
        if res.get("answer_preview"):
            print("     答: %s" % res["answer_preview"])

    # ---------- 汇总 ----------
    n = len(results)
    ok_n = sum(1 for r in results if r.get("ok"))
    tk = {"prompt": 0, "completion": 0, "total": 0, "calls": 0}
    for r in results:
        u = r.get("usage") or {}
        tk["prompt"] += u.get("prompt_tokens", 0)
        tk["completion"] += u.get("completion_tokens", 0)
        tk["total"] += u.get("total_tokens", 0)
        tk["calls"] += u.get("calls", 0)
    facts_total = sum(len(r.get("facts") or []) for r in results)
    facts_ok = sum(len(r.get("facts_hit") or []) for r in results)

    print("\n" + "=" * 68)
    print("工具命中：%d/%d = %.1f%%" % (ok_n, n, (100.0 * ok_n / n) if n else 0))
    if facts_total:
        print("事实命中：%d/%d = %.1f%%（宽松子串匹配，仅供参考）"
              % (facts_ok, facts_total, 100.0 * facts_ok / facts_total))
    print("token：prompt=%d  completion=%d  total=%d  模型调用=%d 次"
          % (tk["prompt"], tk["completion"], tk["total"], tk["calls"]))
    if args.price:
        try:
            pin, pout = [float(x) for x in args.price.split(",")]
            cost = tk["prompt"] / 1e6 * pin + tk["completion"] / 1e6 * pout
            print("成本估算：%.4f 元（按给定单价 输入%.2f/输出%.2f 元每百万）" % (cost, pin, pout))
        except Exception:  # noqa: BLE001
            print("⚠️ --price 解析失败，应为「输入,输出」如 0.5,1.5")
    print("总耗时：%.1fs" % (time.time() - t_all))
    print("=" * 68)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"provider": provider, "model": model, "summary":
                       {"tools_hit": ok_n, "total": n, "facts_hit": facts_ok,
                        "facts_total": facts_total, "tokens": tk},
                       "results": results}, f, ensure_ascii=False, indent=2)
        print("结果已写入：%s" % args.json)

    return 0 if ok_n == n else 1


if __name__ == "__main__":
    sys.exit(main())
