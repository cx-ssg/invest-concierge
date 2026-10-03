# -*- coding: utf-8 -*-
"""E-R1 · `kb_dup6b` 零新内容对照 —— **可复跑夹具**（F 阶段的回归夹具）。

## 它锁的是什么

E1 报告 §6/§9 曾把 A3a 0.922→0.725 归因成「判据对**语料规模**敏感」。codex F2 用本对照证伪：
把**同一批 20 篇茅台文档原样复制 6 份**（文本逐字节相同、向量逐字节相同 ⇒ **零新内容**，
N=281→1686，规模 +6×）——

  * A3a **不动**（E1 口径：47/51 = 0.922，与 pre 相同；F0b 口径：50/51，同样 Δ=0）；
  * 真实语料的翻转 **0 条**被复现（E1：16 条中 10 条 out_of_domain → 0/10；F0b：18 条中 6 条 → 0/6）；
  * 对照自身只有**反向** `weak→none` 变化（E1：6 条；F0b：16 条 —— 多出来的部分来自
    `SAR_NONE` 0.06→0.075 之后 idf 的 0.5 平滑残差**开始跨过阈值**，见 CHK3/CHK5）。

⇒ 真机制是判据统计量对**语料内容异质性**敏感（`idf(N,df)` 与 `v1` 的相对带宽
`max(1, N·ρ)` 都**不是尺度不变的**），**不是**"库变大"。
⚠️ F0b 只改了 `ρ`（0.05→0.02）与阈值（0.075/0.45）：`v1` 一侧已作到**纯复制逐位不变**，
但 `SAR` 一侧的 idf 平滑残差**仍在**（它正是 CHK2 的 tol 会被顶到的地方 —— S=0.08 时
`irr-0354` 的 base/ctrl sar = 0.0891/0.0761 跨阈值 ⇒ Δ(A3a)=0.020 → CHK2 FAIL）。

## 为什么它是 F 阶段的回归夹具

F 阶段若按「规模敏感」去调阈值（只动 `SAR_NONE`/`V1_NONE`），会修不到点：
这几条越界靠的是 `v1` 的相对带宽（与内容分布绑定）。本夹具把
「**纯扩规模 ≠ 翻转**」变成可执行断言 —— 任何人再引用"越大越容易放行"都必须先过它。

## 记录臂（2026-10-03 F0b 起按**判据口径**分档）

strict 模式校验的是「**当前判据口径下的记录值**」——判据改了口径，记录值必然变：
不是把锁改松，三条**不变量**（结构/零新内容、纯复制 Δ(A3a)=0、真实翻转 0 条被复现）一条没动。

| 记录臂 | 判据 (ρ, SAR_NONE, V1_NONE) | A3a base/ctrl | 真实翻转 (OOD) |
|---|---|---|---|
| `E1`（E 阶段自证） | (0.05, 0.06, 0.35) | 47/51 · 47/51 | 16（10） |
| `F0b`（本阶段修 v1 相对带） | (0.02, 0.075, 0.45) | 50/51 · 50/51 | 18（6） |

`--arm auto`（默认）按当前生产判据三元组选臂；**匹配不上则关闭 strict 并告警**
（防止将来改了判据却拿旧常数"验"自己）。详见 `report-F0b.md`。

## 用法

```
python scripts/e_r1_kb_dup6b.py                      # 按当前判据自动选记录臂（默认 pre-E1 备份 + 现库）
python scripts/e_r1_kb_dup6b.py --base X.db --real Y.db --work .e-r1   # F 阶段的任意两库回归
python scripts/e_r1_kb_dup6b.py --no-strict          # 只打印不校验记录值（换库时用）
python scripts/e_r1_kb_dup6b.py --arm E1             # 指定记录臂（判据不匹配时自动降级为非 strict）
```

**只读**打开源库（`mode=ro&immutable=1`，不写 kb.db、不产生 -wal/-shm）；
对照库写到 `--work`（默认 `.e-r1/`，`*.db` 已被 .gitignore 忽略）。
不联网、不调用 embedding、不改任何生产代码。

退出码：0 = 全部检查通过；1 = 有检查失败。
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from utils.rag.evidence import EvidenceJudge          # noqa: E402
from utils.rag.evidence import feature_band           # noqa: E402
from utils.rag import evidence as ev_mod              # noqa: E402
from utils.rag.tokenize import tokenize               # noqa: E402
from utils.rag import store as rag_store              # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden", "rag")

PRE_E1_SHA1 = "57ccee1906de76f70020992a9997b7d8e2bbec67"

# ⚠️ 2026-10-03 F0b：**记录臂按判据口径分档**。
# 为什么需要它：本夹具的 strict 校验把「当时判据下的实测值」钉成常数；F0b 有意改了
# 判据（特征词带 ρ 0.05→0.02、`SAR_NONE` 0.06→0.075、`V1_NONE` 0.35→0.45）⇒
# 旧常数**必然**失效。**不是把锁改松**：三条**不变量**（结构/零新内容、
# Δ(A3a)=0、真实翻转 0 条被纯复制复现）一条没动；变的只是"这套判据下的记录值"。
# 旧记录（E1）**保留在案**并随运行打印，便于逐位对账。
# 选档依据 = **当前生产判据**（`(FEATURE_DF_FRACTION, SAR_NONE, V1_NONE)` 三元组），
# 匹配不上任何档 ⇒ **关闭 strict 并告警**（防将来改了判据却拿旧常数"验"自己）。
ARMS = {
    "E1 (2026-10-03 E 阶段自证)": {
        "predicate": (0.05, 0.06, 0.35),
        "expect": {
            "base_chunks": 281,
            "control_chunks": 1686,
            "control_a3a": (47, 51),
            "control_changes": 6,
            "real_flips": 16,
            "real_flips_by_kind": {"out_of_domain": 10,
                                   "in_domain_unanswerable": 3,
                                   "near_miss": 3},
            "reproduced": 0,
            "ood_reproduced": 0,
        },
    },
    "F0b (2026-10-03 修 v1 相对带)": {
        "predicate": (0.02, 0.075, 0.45),
        "expect": {
            "base_chunks": 281,
            "control_chunks": 1686,
            "control_a3a": (50, 51),
            "control_changes": 16,
            "real_flips": 18,
            "real_flips_by_kind": {"out_of_domain": 6,
                                   "in_domain_unanswerable": 7,
                                   "near_miss": 5},
            "reproduced": 0,
            "ood_reproduced": 0,
        },
    },
}
EXPECT = ARMS["E1 (2026-10-03 E 阶段自证)"]["expect"]
# ⚠️ 上行的 `EXPECT` **不再被 main() 使用**（main 按 `pick_arm()` 选臂）——
# 保留它是为了任何外部/历史引用不至于 `ImportError`；**新增逻辑请走 `pick_arm()`**。
DEFAULT_BASE = "D:/Vault/Handoff/itt-20261002/e1-scratch/pre_e1_kb.db"


def current_predicate():
    """当前生产判据的三元组 —— 用来选记录臂。"""
    return (getattr(ev_mod, "FEATURE_DF_FRACTION", 0.05),
            ev_mod.SAR_NONE, ev_mod.V1_NONE)


def pick_arm(name="auto"):
    """返回 `(arm_name, expect, matched)`。`matched=False` ⇒ strict 必须关闭。"""
    cur = current_predicate()
    for arm_name, arm in ARMS.items():
        if name != "auto" and not arm_name.startswith(name):
            continue
        if name == "auto" and arm["predicate"] != cur:
            continue
        return arm_name, arm["expect"], True
    # 显式指定但判据不匹配：仍返回该臂，但标记不匹配（调用方关 strict）
    for arm_name, arm in ARMS.items():
        if name != "auto" and arm_name.startswith(name):
            return arm_name, arm["expect"], (arm["predicate"] == cur)
    return "（未知判据 %r）" % (cur,), None, False



def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def ro(path):
    """只读 immutable 连接 —— 绝不 writable，避免动到语料库。"""
    if not os.path.isfile(path):
        raise SystemExit("[FATAL] 库不存在：%s" % path)
    c = sqlite3.connect("file:%s?mode=ro&immutable=1" % path.replace("\\", "/"), uri=True)
    c.row_factory = sqlite3.Row
    return c


def build_control(base_path, dst_path, copies):
    """把 base 的每篇文档**原样复制 copies 份**（文本/向量逐字节相同，零新内容）。"""
    if os.path.exists(dst_path):
        os.remove(dst_path)
    for side in ("-wal", "-shm"):
        if os.path.exists(dst_path + side):
            os.remove(dst_path + side)
    src = ro(base_path)
    dst = sqlite3.connect(dst_path)
    dst.executescript("""
CREATE TABLE documents(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, source TEXT,
  title TEXT, url TEXT, published_at TEXT, file_path TEXT, sha256 TEXT, created_at TEXT);
CREATE TABLE chunks(id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL,
  seq INTEGER NOT NULL, text TEXT NOT NULL, is_table INTEGER NOT NULL DEFAULT 0,
  page_no INTEGER, token_len INTEGER);
CREATE TABLE embeddings(chunk_id INTEGER PRIMARY KEY, model TEXT, dim INTEGER, vec BLOB);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
""")
    docs = src.execute("SELECT * FROM documents ORDER BY id").fetchall()
    by_doc = {}
    for ch in src.execute("SELECT * FROM chunks ORDER BY id"):
        by_doc.setdefault(ch["doc_id"], []).append(ch)
    embs = {r["chunk_id"]: r for r in src.execute("SELECT * FROM embeddings")}
    for _ in range(copies):
        for d in docs:
            cur = dst.execute(
                "INSERT INTO documents(code,source,title,url,published_at,file_path,sha256,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (d["code"], d["source"], d["title"], d["url"], d["published_at"],
                 d["file_path"], d["sha256"], d["created_at"]))
            nid = cur.lastrowid
            for ch in by_doc.get(d["id"], []):
                cur2 = dst.execute(
                    "INSERT INTO chunks(doc_id,seq,text,is_table,page_no,token_len)"
                    " VALUES(?,?,?,?,?,?)",
                    (nid, ch["seq"], ch["text"], ch["is_table"], ch["page_no"],
                     ch["token_len"]))
                e = embs.get(ch["id"])
                if e is not None:
                    dst.execute(
                        "INSERT INTO embeddings(chunk_id,model,dim,vec) VALUES(?,?,?,?)",
                        (cur2.lastrowid, e["model"], e["dim"], e["vec"]))
        dst.commit()
    dst.execute("INSERT INTO meta(key,value) VALUES('schema_version','2')")
    dst.commit()
    dst.close()
    src.close()


def judge_of(path):
    c = ro(path)
    try:
        meta, _ = rag_store.load_index(c)
    finally:
        c.close()
    return EvidenceJudge([tokenize(m.get("text") or "") for m in meta]), len(meta)


def block_signature(path, doc_limit=None):
    """按 (文档序号, seq) 排序的 (text, is_table, page_no, token_len, vec) 序列。

    ⚠️ 不能按 `c.id` 排序来对齐两份库 —— base 的 chunk_id 不是 (doc_id,seq) 单调的
    （E1 语料由多批 ingest 累积，doc 2 的块 id 曾被后一批插到更大 id）。
    这里统一按 **documents.id 的升序名次** 归一，`doc_limit` 取前 N 篇（对照库的一份副本）。
    """
    c = ro(path)
    try:
        rows = [(r["did"], r["seq"], r["text"], r["is_table"], r["page_no"],
                 r["token_len"], r["vec"])
                for r in c.execute(
                    "SELECT d.id AS did, c.seq, c.text, c.is_table, c.page_no, c.token_len, e.vec "
                    "FROM chunks c JOIN documents d ON d.id=c.doc_id "
                    "LEFT JOIN embeddings e ON e.chunk_id=c.id ORDER BY d.id, c.seq")]
    finally:
        c.close()
    order = sorted({r[0] for r in rows})
    if doc_limit is not None:
        order = order[:doc_limit]
    rank = {d: i for i, d in enumerate(order)}
    return [(rank[r[0]],) + r[1:] for r in rows if r[0] in rank]


def main(argv=None):
    ap = argparse.ArgumentParser(description="E-R1 kb_dup6b 零新内容对照夹具（F 阶段回归夹具）")
    ap.add_argument("--base", default=DEFAULT_BASE,
                    help="baseline 库（默认 pre-E1 备份副本，sha1=%s…）" % PRE_E1_SHA1[:8])
    ap.add_argument("--real", default=os.path.join(ROOT, "kb.db"),
                    help="真实扩语料后的库（默认仓库 kb.db）")
    ap.add_argument("--work", default=os.path.join(ROOT, ".e-r1"))
    ap.add_argument("--copies", type=int, default=6)
    ap.add_argument("--tol", type=float, default=0.0,
                    help="A3a 聚合量的允许漂移（0 = 必须完全相同）")
    ap.add_argument("--strict", dest="strict", action="store_true", default=None,
                    help="校验当前**判据口径**记录臂的全部实测值（默认：base 为记录备份时自动开启）")
    ap.add_argument("--no-strict", dest="strict", action="store_false")
    ap.add_argument("--arm", default="auto", choices=["auto", "E1", "F0b"],
                    help="记录臂口径（auto = 按当前生产判据三元组自动匹配；匹配不上则关 strict）")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    arm_name, expect, arm_matched = pick_arm(args.arm)

    with open(os.path.join(GOLDEN, "queries_holdout_irr.json"), encoding="utf-8") as f:
        irr = json.load(f)
    assert len(irr) == 113, "[FATAL] holdout 负例应为 113 条，实际 %d" % len(irr)

    base_sha = sha1_file(args.base)
    if args.strict is None:
        # 默认：base 是 E1 记录备份 **且**判据口径能匹配上记录臂 → 开 strict
        args.strict = (base_sha == PRE_E1_SHA1) and arm_matched
    if args.strict and not arm_matched:
        print("[WARN] strict 已关闭：当前生产判据 %r 不匹配任何记录臂 %r"
              % (current_predicate(), [a["predicate"] for a in ARMS.values()]))
        args.strict = False
    os.makedirs(args.work, exist_ok=True)
    ctrl_path = os.path.join(args.work, "kb_dup6b.db")

    print("=== E-R1 · kb_dup6b 零新内容对照夹具 ===")
    print("copies=%d  strict=%s  记录臂=%s" % (args.copies, args.strict, arm_name))
    print("当前生产判据 (ρ, SAR_NONE, V1_NONE) = %r" % (current_predicate(),))
    for a_name, a in ARMS.items():
        tag = "  <== 本次记录臂" if a_name == arm_name else ""
        print("  记录臂 %-28s 判据=%r  A3a(base,ctrl)=%s  real_flips=%d%s"
              % (a_name, a["predicate"], a["expect"]["control_a3a"],
                 a["expect"]["real_flips"], tag))
    print("base  %s  sha1=%s" % (args.base, base_sha))
    print("real  %s  sha1=%s" % (args.real, sha1_file(args.real)))
    build_control(args.base, ctrl_path, args.copies)
    print("ctrl  %s  (built: %d 份逐字节复制)" % (ctrl_path, args.copies))

    jb, n_base = judge_of(args.base)
    jc, n_ctrl = judge_of(ctrl_path)
    jr, n_real = judge_of(args.real)
    print("chunks: base=%d  ctrl=%d  real=%d" % (n_base, n_ctrl, n_real))

    # ---- [CHK1] 结构 + 零新内容（文本/向量字节）----
    ok1 = (n_ctrl == n_base * args.copies)
    _c = ro(args.base)
    try:
        n_docs = _c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    finally:
        _c.close()
    sig_base = block_signature(args.base)
    sig_ctrl = block_signature(ctrl_path, doc_limit=n_docs)
    dup_ok = (sig_base == sig_ctrl and len(sig_ctrl) * args.copies == n_ctrl)
    if not dup_ok:
        print("  [!] 对照库副本1（%d 篇/%d 块）与 base（%d 块）不一致"
              % (n_docs, len(sig_ctrl), len(sig_base)))
    print("[CHK1] 结构 base×%d=%d == ctrl %d ....... %s"
          % (args.copies, n_base * args.copies, n_ctrl, "PASS" if ok1 else "FAIL"))
    print("       副本1 的 %d 块 文本+向量 与 base 逐字节相同 ....... %s"
          % (len(sig_base), "PASS" if dup_ok else "FAIL"))

    # ---- 三个臂的档位 ----
    def levels(j):
        out = {}
        for r in irr:
            e = j.assess(r["query"])
            out[r["id"]] = (e.level, round(e.sar, 4), round(e.v1, 3))
        return out

    lv_b, lv_c, lv_r = levels(jb), levels(jc), levels(jr)
    ood = [r["id"] for r in irr if r.get("kind") == "out_of_domain"]
    a3a = lambda lv: (sum(1 for i in ood if lv[i][0] == "none"), len(ood))

    b_a3a, c_a3a, r_a3a = a3a(lv_b), a3a(lv_c), a3a(lv_r)
    print("[CHK2] A3a: base %d/%d=%.3f  ctrl %d/%d=%.3f  real %d/%d=%.3f"
          % (b_a3a[0], b_a3a[1], b_a3a[0] / b_a3a[1],
             c_a3a[0], c_a3a[1], c_a3a[0] / c_a3a[1],
             r_a3a[0], r_a3a[1], r_a3a[0] / r_a3a[1]))
    a3a_delta = abs(c_a3a[0] / c_a3a[1] - b_a3a[0] / b_a3a[1])
    ok2 = a3a_delta <= args.tol
    print("       纯复制 Δ(A3a) = %.3f <= tol %.3f ....... %s"
          % (a3a_delta, args.tol, "PASS" if ok2 else "FAIL"))
    if args.strict and (b_a3a, c_a3a) != (expect["control_a3a"], expect["control_a3a"]):
        ok2 = False
        print("       [!] strict: 期望 base/ctrl A3a = %s" % (expect["control_a3a"],))

    # ---- [CHK3] 对照库自身的档位变化（应为反向 weak→none）----
    c_changes = [i for i in lv_b if lv_b[i][0] != lv_c[i][0]]
    c_dirs = sorted({"%s->%s" % (lv_b[i][0], lv_c[i][0]) for i in c_changes})
    print("[CHK3] 对照库自身档位变化 = %d/113 方向=%s（应全为 weak->none）"
          % (len(c_changes), c_dirs))
    ok3 = all(lv_b[i][0] == "weak" and lv_c[i][0] == "none" for i in c_changes)
    if args.strict and len(c_changes) != expect["control_changes"]:
        ok3 = False
        print("       [!] strict: 期望 %d 条" % expect["control_changes"])
    print("       方向全为反向 weak->none ....... %s" % ("PASS" if ok3 else "FAIL"))
    for i in sorted(c_changes):
        print("         %-9s %-24s %s -> %s" % (
            i, next(r.get("kind") for r in irr if r["id"] == i),
            lv_b[i], lv_c[i]))

    # ---- [CHK4] 真实翻转 vs 控制组复现 0 条 ----
    real_flips = [i for i in lv_b if lv_b[i][0] != lv_r[i][0]]
    by_kind = {}
    for i in real_flips:
        k = next(r.get("kind") for r in irr if r["id"] == i)
        by_kind[k] = by_kind.get(k, 0) + 1
    reproduced = [i for i in real_flips if lv_c[i][0] == lv_r[i][0]]
    ood_flips = [i for i in real_flips if i in ood]
    ood_rep = [i for i in ood_flips if lv_c[i][0] == lv_r[i][0]]
    print("[CHK4] 真实翻转 real vs base = %d 条  by_kind=%s" % (len(real_flips), by_kind))
    print("       被零新内容对照复现 = %d/%d（out_of_domain %d/%d）"
          % (len(reproduced), len(real_flips), len(ood_rep), len(ood_flips)))
    ok4 = (len(reproduced) == 0)
    if args.strict:
        ok4 = ok4 and len(real_flips) == expect["real_flips"] \
            and by_kind == expect["real_flips_by_kind"]
    print("       ⇒ 翻转由「异质新内容」而非「规模」造成 ....... %s" % ("PASS" if ok4 else "FAIL"))

    # ---- [CHK5] 机制：v1 特征词带（**生产实现** `feature_band`）与逐 token df ----
    # ⚠️ 2026-10-03 F0b：这里原先硬编码 `max(1, 0.05N)`（判据的旧带宽）。
    #    带宽是 F0b 的**被测对象**，硬编码会让"改了生产带宽、夹具还印旧值"——
    #    故改为调用**同一个** `evidence.feature_band`（与判据共享唯一实现）。
    band = feature_band
    print("[CHK5] v1 特征词带 feature_band(N)=max(1, N·ρ), ρ=%s: base=%.2f ctrl=%.2f real=%.2f"
          % (getattr(ev_mod, "FEATURE_DF_FRACTION", "?"),
             band(n_base), band(n_ctrl), band(n_real)))
    print("       纯复制下带宽同倍放大（ctrl/base=%.3f，理想值=%.3f）⇒ 准入词集合不变"
          % (band(n_ctrl) / band(n_base), n_ctrl / n_base))
    for qid in ("irr-0306", "irr-0331", "irr-0357", "irr-0120"):
        q = next(r["query"] for r in irr if r["id"] == qid)
        toks = sorted(set(tokenize(q)))
        cells = []
        for t in toks:
            db_, dc_, dr_ = jb.index.df.get(t, 0), jc.index.df.get(t, 0), jr.index.df.get(t, 0)
            if db_ != dr_ or db_ == 0:
                entered = (db_ > band(n_base)) and (dr_ <= band(n_real))
                cells.append("%s:df %d->%d(ctrl %d)%s"
                             % (t, db_, dr_, dc_, "^进带" if entered else ""))
        print("       %s %s" % (qid, q[:22]))
        print("         %s" % ("; ".join(cells) or "（token df 无变化）"))
    band_ok = abs(band(n_ctrl) / band(n_base) - n_ctrl / n_base) < 1e-9
    print("       带宽随块数同倍放大（尺度不变）....... %s" % ("PASS" if band_ok else "FAIL"))
    print("       ^进带 = df 从 band_base(%.2f) 之外进入 band_real(%.2f) 之内 —— v1 的分母变大、比值跳变"
          % (band(n_base), band(n_real)))

    all_ok = ok1 and bool(dup_ok) and ok2 and ok3 and ok4 and band_ok
    print("")
    print("kb_dup6b RESULT: %s" % ("OK" if all_ok else "FAIL"))
    print("  判读：A3a 在**纯复制**下不动（%d/%d）；真实语料 %d 条翻转 %d 条被复现"
          % (c_a3a[0], c_a3a[1], len(real_flips), len(reproduced)))
    print("        ⇒ 归因是「语料内容异质性」下的 idf/带宽非尺度不变，**不是「库变大」**")
    if expect:
        print("        记录臂 %s：真实翻转记录值 %d（OOD %d）｜本次实测 %d（OOD %d）"
              % (arm_name, expect["real_flips"],
                 expect["real_flips_by_kind"].get("out_of_domain", 0),
                 len(real_flips), by_kind.get("out_of_domain", 0)))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"base": args.base, "real": args.real, "control": ctrl_path,
                       "arm": arm_name, "predicate": current_predicate(),
                       "n_base": n_base, "n_ctrl": n_ctrl, "n_real": n_real,
                       "a3a": {"base": b_a3a, "ctrl": c_a3a, "real": r_a3a},
                       "control_changes": c_changes, "real_flips": real_flips,
                       "real_flips_by_kind": by_kind, "reproduced_by_control": reproduced,
                       "pass": all_ok}, f, ensure_ascii=False, indent=1)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
