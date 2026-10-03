# -*- coding: utf-8 -*-
"""三库在线备份 + **备份可用性自证**（stdlib only，H3 恢复 SOP 的日常工具）。

为什么不是 `Copy-Item`：`kb.db` / `fund_agent.db` 都是 **WAL 模式**，应用在线时直接拷主文件
可能拿到「主文件 + 未合并的 -wal」的**不一致快照**（拷到的库缺最近提交，或直接打不开）。
本脚本走 SQLite 官方 **online backup API**（`sqlite3.Connection.backup`）：
对源库加读锁逐页复制 ⇒ 即使有并发写也能拿到**事务一致**的快照，且**不写源库一字节**。

用法：
  python scripts/backup_db.py                          # 备份三库到 backups/ 并逐个自证
  python scripts/backup_db.py --out D:/bak --keep 14   # 自定义目录/保留份数
  python scripts/backup_db.py --db kb.db --db fund_agent.db
  python scripts/backup_db.py --verify backups/kb.db.20261003-235959.bak
                                                       # 只验证一个备份能不能用

自证口径（「有个文件就叫备份」不算数）：
  ① `PRAGMA integrity_check` == ok；② 逐表行数；③ 源库与备份的**内容指纹**一致
  （逐行 repr 哈希 —— 字节哈希会因页布局不同而假报警，见 report-H3.md）。
退出码：0=全部通过；1=有库失败；2=参数/路径错误。
"""
import argparse
import hashlib
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DBS = ("kb.db", "fund_agent.db", "checkpoints.db")
#: 各库的比对表（「内容指纹」用；不存在则跳过）
FINGERPRINT_TABLES = {
    "kb.db": ("documents", "chunks", "embeddings", "meta"),
    "fund_agent.db": ("agent_sessions", "agent_messages", "memories", "app_settings",
                      "diary_entries", "alerts", "reports"),
    "checkpoints.db": ("checkpoints", "writes"),
}


def ro(path):
    """只读打开，**零足迹**。

    WAL 模式的库若用 `mode=ro` 打开，SQLite 会创建（0 字节的）`-wal` 与 `-shm` 边车文件
    ——主库 SHA1 不变，但会在生产目录留下垃圾（H3 演练实测：mode=ro 打开三库后
    冒出 3 组 `-shm`/`-wal`）。故此处分两档：
      · 无 `-wal`（静止态）⇒ `immutable=1`：完全不建边车文件、不取锁；
      · 有 `-wal`（应用在线写过）⇒ `mode=ro`：必须让 SQLite 走真实 WAL 读，`immutable`
        会读到**撕裂快照**。边车文件此时本来就在。
    两档都**不写主库**（备份 API 只读源库）。
    """
    if not os.path.exists(path + "-wal"):
        return sqlite3.connect(
            "file:{}?mode=ro&immutable=1".format(path.replace("\\", "/")), uri=True)
    return sqlite3.connect(
        "file:{}?mode=ro".format(path.replace("\\", "/")), uri=True)


def integrity(conn):
    return conn.execute("PRAGMA integrity_check").fetchone()[0]


def tables(conn):
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def rowcounts(conn, skip_meta=True):
    out = {}
    for t in tables(conn):
        if t == "sqlite_sequence":
            continue
        out[t] = conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
    return out


def digest(conn, dbname):
    """内容指纹：对约定表逐行做稳定 repr 哈希（与页布局、VACUUM 无关）。"""
    h = hashlib.sha1()
    for t in FINGERPRINT_TABLES.get(dbname, ()):
        try:
            rows = conn.execute('SELECT * FROM "%s" ORDER BY 1' % t)
        except sqlite3.Error:
            continue
        for row in rows:
            h.update(repr(tuple(row)).encode("utf-8", "replace"))
    return h.hexdigest()


def verify(dbname, path):
    """返回 (ok, 明细 dict)。任何一项不过 ⇒ ok=False。"""
    det = {"path": path, "bytes": os.path.getsize(path)}
    try:
        c = ro(path)
    except sqlite3.Error as e:
        return False, dict(det, error="%s: %s" % (type(e).__name__, e))
    try:
        # ⚠️ 每一步都可能抛 DatabaseError（「database disk image is malformed」），
        # 校验器**必须**把它变成 ok=False 而不是自己崩掉 —— H3 反向控制实测抓到的 bug：
        # 截断的备份让 tables() 抛异常，旧实现直接 Traceback（校验器比坏备份先死）。
        det["tables"] = len(tables(c))
        det["rowcounts"] = rowcounts(c)
        try:
            det["integrity_check"] = integrity(c)
        except sqlite3.Error as e:
            det["integrity_check"] = "ERR %s: %s" % (type(e).__name__, e)
        det["content_digest"] = digest(c, dbname)
    except sqlite3.Error as e:
        det["integrity_check"] = det.get("integrity_check") or "ERR %s: %s" % (
            type(e).__name__, e)
        det["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        c.close()
    ok = det.get("integrity_check") == "ok"
    return ok, det


def backup_one(dbname, src_dir, out_dir, verify_content=True):
    src = os.path.join(src_dir, dbname)
    if not os.path.exists(src):
        return False, {"error": "源库不存在：%s" % src}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(out_dir, "%s.%s.bak" % (dbname, stamp))
    t0 = time.time()
    s = ro(src)
    try:
        if verify_content:
            with ro(src) as c2:
                src_digest = digest(c2, dbname)
                src_counts = rowcounts(c2)
        d = sqlite3.connect(dst)
        try:
            s.backup(d)                      # 官方在线备份：事务一致快照
        finally:
            d.close()
    finally:
        s.close()
    ok, det = verify(dbname, dst)
    det["backup_s"] = round(time.time() - t0, 3)
    det["dst"] = dst
    if verify_content:
        det["src_counts"] = src_counts
        det["rowcounts_match"] = det.get("rowcounts") == src_counts
        det["content_digest_match"] = det.get("content_digest") == src_digest
        ok = ok and det["rowcounts_match"] and det["content_digest_match"]
    return ok, det


def rotate(out_dir, keep):
    """每个库只留最近 keep 份（按文件名时间戳排序，删最旧）。"""
    removed = []
    for dbname in DEFAULT_DBS:
        files = sorted(f for f in os.listdir(out_dir) if f.startswith(dbname + "."))
        for f in files[:-keep] if keep > 0 else []:
            os.remove(os.path.join(out_dir, f))
            removed.append(f)
    return removed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=ROOT, help="生产库目录（默认项目根）")
    ap.add_argument("--out", default=os.path.join(ROOT, "backups"), help="备份目录")
    ap.add_argument("--db", action="append", default=None, help="只备份指定库（可重复）")
    ap.add_argument("--keep", type=int, default=7, help="每个库保留最近 N 份（0=不轮转）")
    ap.add_argument("--verify", default=None, help="只验证一个已有备份文件")
    ap.add_argument("--no-content-check", action="store_true",
                    help="跳过内容指纹比对（源库当前不可读时用）")
    args = ap.parse_args(argv)

    if args.verify:
        name = os.path.basename(args.verify)
        dbname = next((d for d in DEFAULT_DBS if name.startswith(d)), "kb.db")
        ok, det = verify(dbname, args.verify)
        print("[verify] %s -> %s" % ("OK" if ok else "FAIL", det))
        return 0 if ok else 1

    os.makedirs(args.out, exist_ok=True)
    dbs = args.db or list(DEFAULT_DBS)
    all_ok = True
    for name in dbs:
        ok, det = backup_one(name, args.src, args.out,
                             verify_content=not args.no_content_check)
        all_ok = all_ok and ok
        print("[backup] %s %s bytes=%s s=%s integrity=%s counts=%s match=%s"
              % ("OK" if ok else "FAIL", name, det.get("bytes"), det.get("backup_s"),
                 det.get("integrity_check"), det.get("rowcounts"), det.get("dst")))
        if not ok:
            print("         detail=%s" % {k: v for k, v in det.items()
                                          if k != "rowcounts"})
    gone = rotate(args.out, args.keep)
    if gone:
        print("[backup] 轮转删除 %d 份：%s" % (len(gone), gone))
    print("[backup] RESULT: %s -> %s" % ("OK" if all_ok else "FAIL", args.out))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
