# -*- coding: utf-8 -*-
"""kb.db 持久化层（M1 内核）。

位置：与主库并列 `config._DATA_DIR/kb.db`（`config.py:101` 定义 DB_FILE 同目录）。
`.gitignore` 已含 `*.db`，语料与索引天然不入仓库。

**无 FTS5 表**（中文实测不可用，见 `COVERAGE_DESIGN.md` §3.3 第 3 条）。
BM25 索引不落表 —— 启动时由 `chunks` 现建（万级块内存可接受），避免双份数据与索引漂移。
"""
import os
import sqlite3

import numpy as np

SCHEMA_VERSION = 1
VEC_DTYPE = "float32"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT,
    source       TEXT,
    title        TEXT,
    url          TEXT,
    published_at TEXT,
    file_path    TEXT,
    sha256       TEXT,
    created_at   TEXT,
    UNIQUE(code, source, published_at)
);
CREATE TABLE IF NOT EXISTS chunks(
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id    INTEGER NOT NULL,
    seq       INTEGER NOT NULL,
    text      TEXT NOT NULL,
    is_table  INTEGER NOT NULL DEFAULT 0,
    page_no   INTEGER,
    token_len INTEGER,
    UNIQUE(doc_id, seq)
);
CREATE TABLE IF NOT EXISTS embeddings(
    chunk_id INTEGER PRIMARY KEY,
    model    TEXT,
    dim      INTEGER,
    vec      BLOB
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
"""


def db_path(path=None):
    """默认与 fund_agent.db 同目录。"""
    if path:
        return path
    from config import _DATA_DIR
    return os.path.join(_DATA_DIR, "kb.db")


def get_conn(path=None):
    conn = sqlite3.connect(db_path(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ensure_schema(conn):
    """建表 + 记录 schema_version（幂等）。"""
    conn.executescript(SCHEMA)
    set_meta(conn, "schema_version", str(SCHEMA_VERSION))
    conn.commit()
    return conn


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def upsert_document(conn, doc):
    """幂等键 (code, source, published_at)。返回 documents.id。"""
    conn.execute(
        "INSERT INTO documents(code, source, title, url, published_at, "
        "file_path, sha256, created_at) VALUES(?,?,?,?,?,?,?, datetime('now')) "
        "ON CONFLICT(code, source, published_at) DO UPDATE SET "
        "title=excluded.title, url=excluded.url, file_path=excluded.file_path, "
        "sha256=excluded.sha256",
        (doc.get("code"), doc.get("source"), doc.get("title"), doc.get("url"),
         doc.get("published_at"), doc.get("file_path"), doc.get("sha256")),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM documents WHERE code IS ? AND source IS ? AND published_at IS ?",
        (doc.get("code"), doc.get("source"), doc.get("published_at")),
    ).fetchone()
    return row["id"] if row else None


def insert_chunks(conn, doc_id, chunks):
    """写入块（幂等键 (doc_id, seq)），返回 chunk_id 列表。"""
    ids = []
    for c in chunks:
        conn.execute(
            "INSERT INTO chunks(doc_id, seq, text, is_table, page_no, token_len) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(doc_id, seq) DO UPDATE SET text=excluded.text, "
            "is_table=excluded.is_table, token_len=excluded.token_len",
            (doc_id, c.get("seq", 0), c.get("text", ""), 1 if c.get("is_table") else 0,
             c.get("page_no"), c.get("token_len") or len(c.get("text", ""))),
        )
    conn.commit()
    for c in chunks:
        row = conn.execute(
            "SELECT id FROM chunks WHERE doc_id=? AND seq=?", (doc_id, c.get("seq", 0))
        ).fetchone()
        if row:
            ids.append(row["id"])
    return ids


def save_embeddings(conn, chunk_ids, vecs, model="bge-m3"):
    """写入向量（float32 BLOB）。数量不一致直接报错，不静默丢数据。"""
    if len(chunk_ids) != len(vecs):
        raise ValueError(
            "chunk_ids 与 vecs 数量不一致：{} vs {}".format(len(chunk_ids), len(vecs))
        )
    for cid, v in zip(chunk_ids, vecs):
        arr = np.asarray(v, dtype=VEC_DTYPE)
        conn.execute(
            "INSERT INTO embeddings(chunk_id, model, dim, vec) VALUES(?,?,?,?) "
            "ON CONFLICT(chunk_id) DO UPDATE SET model=excluded.model, "
            "dim=excluded.dim, vec=excluded.vec",
            (cid, model, int(arr.shape[0]), arr.tobytes()),
        )
    conn.commit()
    return len(chunk_ids)


def load_index(conn):
    """加载检索所需索引，返回 (meta, matrix)。

    - `meta[i]`：`{chunk_id, doc_id, seq, text, is_table, code, source, title, url, published_at}`
    - `matrix`：`np.ndarray` shape (n, dim)；无已嵌入块时返回 (list, None)
    """
    rows = conn.execute(
        "SELECT c.id AS chunk_id, c.doc_id, c.seq, c.text, c.is_table, "
        "       d.code, d.source, d.title, d.url, d.published_at, "
        "       e.vec AS vec, e.dim AS dim "
        "FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "LEFT JOIN embeddings e ON e.chunk_id = c.id "
        "ORDER BY c.doc_id, c.seq"
    ).fetchall()

    meta, vecs = [], []
    for r in rows:
        meta.append({
            "chunk_id": r["chunk_id"], "doc_id": r["doc_id"], "seq": r["seq"],
            "text": r["text"], "is_table": bool(r["is_table"]),
            "code": r["code"], "source": r["source"], "title": r["title"],
            "url": r["url"], "published_at": r["published_at"],
        })
        if r["vec"] is not None:
            vecs.append(np.frombuffer(r["vec"], dtype=VEC_DTYPE))

    matrix = np.vstack(vecs).astype(VEC_DTYPE) if vecs else None
    return meta, matrix


def stats(conn):
    """索引统计（供 CLI / 诊断用）。"""
    def _one(sql):
        return conn.execute(sql).fetchone()[0]
    return {
        "documents": _one("SELECT COUNT(*) FROM documents"),
        "chunks": _one("SELECT COUNT(*) FROM chunks"),
        "embedded": _one("SELECT COUNT(*) FROM embeddings"),
        "schema_version": get_meta(conn, "schema_version"),
    }
