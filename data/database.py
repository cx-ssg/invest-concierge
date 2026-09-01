# -*- coding: utf-8 -*-
"""
数据库操作层 - 处理 JSON 文件的读写（持仓、日记、提醒设置）
以及 SQLite 数据库操作（自选股等）
"""

import json
import os
import sqlite3
import datetime
from pathlib import Path

from config import MY_FUNDS_FILE, DIARY_FILE, ALERT_SETTINGS_FILE, DB_FILE
from utils.common import is_safe_write_path


# ==================== SQLite 数据库 ====================

def get_conn():
    """获取 SQLite 数据库连接"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库，创建所有表"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT '',
            market TEXT NOT NULL DEFAULT '',
            added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT '',
            quantity REAL NOT NULL DEFAULT 0,
            cost_price REAL NOT NULL DEFAULT 0,
            created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            trade_type TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            quantity REAL NOT NULL DEFAULT 0,
            amount REAL NOT NULL DEFAULT 0,
            trade_date TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fund_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT,
            amount REAL DEFAULT 0,
            cost_nav REAL DEFAULT 0,
            hold_shares REAL DEFAULT 0,
            added_date TEXT,
            note TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS diary_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL DEFAULT '',
            fund_code TEXT NOT NULL DEFAULT '',
            fund_name TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL DEFAULT '',
            amount REAL NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ===== 旧表迁移：fund_holdings 老结构（buy_price/shares）补列并回填 =====
    # （CREATE TABLE IF NOT EXISTS 不会迁移已存在的旧表；老用户的 6 月库缺 cost_nav/hold_shares）
    cursor.execute("PRAGMA table_info(fund_holdings)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "cost_nav" not in existing_cols:
        cursor.execute("ALTER TABLE fund_holdings ADD COLUMN cost_nav REAL DEFAULT 0")
    if "hold_shares" not in existing_cols:
        cursor.execute("ALTER TABLE fund_holdings ADD COLUMN hold_shares REAL DEFAULT 0")
    if {"cost_nav", "buy_price"} <= existing_cols or "buy_price" in existing_cols:
        cursor.execute(
            "UPDATE fund_holdings "
            "SET cost_nav = buy_price "
            "WHERE buy_price IS NOT NULL AND (cost_nav IS NULL OR cost_nav = 0)")
        cursor.execute(
            "UPDATE fund_holdings "
            "SET hold_shares = shares "
            "WHERE shares IS NOT NULL AND (hold_shares IS NULL OR hold_shares = 0)")

    conn.commit()
    conn.close()


# ==================== 自选股操作 ====================

def add_watchlist_stock(code, name="", market=""):
    """添加自选股"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO watchlist (code, name, market) VALUES (?, ?, ?)",
            (code, name, market)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print("添加自选股失败：{}".format(e))
        return False
    finally:
        conn.close()


def remove_watchlist_stock(code):
    """删除自选股"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM watchlist WHERE code = ?", (code,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print("删除自选股失败：{}".format(e))
        return False
    finally:
        conn.close()


def get_watchlist():
    """获取所有自选股列表"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM watchlist ORDER BY added_time DESC")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print("获取自选股列表失败：{}".format(e))
        return []
    finally:
        conn.close()


def is_in_watchlist(code):
    """检查股票是否已在自选股中"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) as cnt FROM watchlist WHERE code = ?", (code,))
        row = cursor.fetchone()
        return row['cnt'] > 0
    except Exception as e:
        print("检查自选股失败：{}".format(e))
        return False
    finally:
        conn.close()


# ==================== 股票持仓操作 ====================

def get_stock_holding(code):
    """获取单只股票持仓"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM stock_holdings WHERE code = ?", (code,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print("获取股票持仓失败：{}".format(e))
        return None
    finally:
        conn.close()


def get_all_stock_holdings():
    """获取所有股票持仓"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM stock_holdings ORDER BY updated_time DESC")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print("获取所有持仓失败：{}".format(e))
        return []
    finally:
        conn.close()


def add_stock_holding(code, name, quantity, cost_price):
    """添加或更新股票持仓（加权平均成本）"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        existing = get_stock_holding(code)
        if existing:
            # 加权平均成本 = (原数量 * 原成本价 + 新数量 * 新价格) / (原数量 + 新数量)
            old_qty = existing['quantity']
            old_cost = existing['cost_price']
            new_qty = old_qty + quantity
            new_cost = (old_qty * old_cost + quantity * cost_price) / new_qty if new_qty > 0 else cost_price
            cursor.execute("""
                UPDATE stock_holdings SET quantity = ?, cost_price = ?, updated_time = CURRENT_TIMESTAMP
                WHERE code = ?
            """, (new_qty, new_cost, code))
        else:
            cursor.execute("""
                INSERT INTO stock_holdings (code, name, quantity, cost_price)
                VALUES (?, ?, ?, ?)
            """, (code, name, quantity, cost_price))
        conn.commit()
        return True
    except Exception as e:
        print("添加股票持仓失败：{}".format(e))
        return False
    finally:
        conn.close()


def reduce_stock_holding(code, quantity):
    """减少股票持仓（卖出）"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        existing = get_stock_holding(code)
        if not existing:
            return False
        new_qty = existing['quantity'] - quantity
        if new_qty <= 0:
            cursor.execute("DELETE FROM stock_holdings WHERE code = ?", (code,))
        else:
            cursor.execute("""
                UPDATE stock_holdings SET quantity = ?, updated_time = CURRENT_TIMESTAMP
                WHERE code = ?
            """, (new_qty, code))
        conn.commit()
        return True
    except Exception as e:
        print("减少股票持仓失败：{}".format(e))
        return False
    finally:
        conn.close()


def delete_stock_holding(code):
    """删除股票持仓"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM stock_holdings WHERE code = ?", (code,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print("删除股票持仓失败：{}".format(e))
        return False
    finally:
        conn.close()


# ==================== 股票交易记录操作 ====================

def add_stock_transaction(code, name, trade_type, price, quantity, amount, trade_date, note=""):
    """添加股票交易记录"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO stock_transactions (code, name, trade_type, price, quantity, amount, trade_date, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (code, name, trade_type, price, quantity, amount, trade_date, note))
        conn.commit()
        return True
    except Exception as e:
        print("添加交易记录失败：{}".format(e))
        return False
    finally:
        conn.close()


def get_stock_transactions(code=None, limit=100):
    """获取股票交易记录"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        if code:
            cursor.execute(
                "SELECT * FROM stock_transactions WHERE code = ? ORDER BY trade_date DESC, created_time DESC LIMIT ?",
                (code, limit)
            )
        else:
            cursor.execute(
                "SELECT * FROM stock_transactions ORDER BY trade_date DESC, created_time DESC LIMIT ?",
                (limit,)
            )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print("获取交易记录失败：{}".format(e))
        return []
    finally:
        conn.close()


# ==================== 基金持仓操作 (SQLite) ====================


def load_my_funds():
    """从 fund_holdings 表加载持仓基金数据，返回 dict {code: {name, code, amount, cost_nav, hold_shares}}"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM fund_holdings ORDER BY code")
        rows = cursor.fetchall()
        funds = {}
        for row in rows:
            r = dict(row)
            code = r['code']
            funds[code] = {
                'name': r['name'],
                'code': code,
                'amount': r['amount'],
                'cost_nav': r['cost_nav'],
                'hold_shares': r['hold_shares'],
            }
        return funds
    except Exception as e:
        print("加载基金持仓失败：{}".format(e))
        return {}
    finally:
        conn.close()


def save_my_funds(funds):
    """将 fund_holdings 表全部替换为传入的基金持仓数据"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM fund_holdings")
        for code, fund in funds.items():
            cursor.execute("""
                INSERT INTO fund_holdings (code, name, amount, cost_nav, hold_shares)
                VALUES (?, ?, ?, ?, ?)
            """, (
                code,
                fund.get('name', ''),
                fund.get('amount', 0),
                fund.get('cost_nav', 0),
                fund.get('hold_shares', 0),
            ))
        conn.commit()
        return True
    except Exception as e:
        print("保存基金持仓失败：{}".format(e))
        conn.rollback()
        return False
    finally:
        conn.close()


# ==================== 兼容旧接口 ====================

import streamlit as st

@st.cache_data(ttl=60)
def load_funds():
    """兼容旧接口：同 load_my_funds()"""
    return load_my_funds()


def save_funds(funds):
    """兼容旧接口：同 save_my_funds()"""
    return save_my_funds(funds)


# ==================== 投资日记操作 (SQLite) ====================


def load_diary():
    """从 diary_entries 表加载投资日记，返回列表"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM diary_entries ORDER BY date DESC, id DESC")
        rows = cursor.fetchall()
        entries = []
        for row in rows:
            r = dict(row)
            entries.append({
                'id': r['id'],
                'date': r['date'],
                'fund_code': r['fund_code'],
                'fund_name': r['fund_name'],
                'action': r['action'],
                'amount': r['amount'],
                'note': r['note'],
            })
        return entries
    except Exception as e:
        print("加载投资日记失败：{}".format(e))
        return []
    finally:
        conn.close()


def save_diary(entries):
    """将 diary_entries 表全部替换为传入的日记数据"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM diary_entries")
        for entry in entries:
            entry_id = entry.get("id")
            if entry_id:
                cursor.execute("""
                    INSERT INTO diary_entries (id, date, fund_code, fund_name, action, amount, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    entry_id,
                    entry.get('date', ''),
                    entry.get('fund_code', ''),
                    entry.get('fund_name', ''),
                    entry.get('action', ''),
                    entry.get('amount', 0),
                    entry.get('note', ''),
                ))
            else:
                # 无 id：交给 AUTOINCREMENT（显式写 0 会撞 UNIQUE 约束）
                cursor.execute("""
                    INSERT INTO diary_entries (date, fund_code, fund_name, action, amount, note)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    entry.get('date', ''),
                    entry.get('fund_code', ''),
                    entry.get('fund_name', ''),
                    entry.get('action', ''),
                    entry.get('amount', 0),
                    entry.get('note', ''),
                ))
        conn.commit()
        return True
    except Exception as e:
        print("保存投资日记失败：{}".format(e))
        conn.rollback()
        return False
    finally:
        conn.close()


# ==================== 涨跌提醒设置 ====================

def load_alert_settings():
    """加载涨跌提醒设置"""
    if not os.path.exists(ALERT_SETTINGS_FILE):
        return {}
    try:
        with open(ALERT_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_alert_settings(settings):
    """保存涨跌提醒设置"""
    if not is_safe_write_path(ALERT_SETTINGS_FILE):
        return False
    try:
        real = os.path.realpath(ALERT_SETTINGS_FILE)
        Path(real).write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except (OSError, TypeError):
        return False


# ==================== 基金持仓单条记录操作 ====================


def load_fund_holdings():
    """返回所有基金持仓列表"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM fund_holdings")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_fund_holding(code, name, amount, cost_nav, hold_shares, note=""):
    """插入或更新一条基金持仓"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO fund_holdings (code, name, amount, cost_nav, hold_shares, note, added_date)
        VALUES (?, ?, ?, ?, ?, ?, date('now'))
        ON CONFLICT(code) DO UPDATE SET
            name=excluded.name,
            amount=excluded.amount,
            cost_nav=excluded.cost_nav,
            hold_shares=excluded.hold_shares,
            note=excluded.note
    """, (code, name, amount, cost_nav, hold_shares, note))
    conn.commit()
    conn.close()


def delete_fund_holding(code):
    """删除一条基金持仓"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM fund_holdings WHERE code=?", (code,))
    conn.commit()
    conn.close()


# ==================== Agent 会话记忆操作 (SQLite) ====================
# Agent MVP 记忆层：跨页持久化对话（ai_chat / 诊断页"AI 追问"共用），
# 所有 SQL 均参数绑定。短连接读写（不存 session_state，避免多标签 check_same_thread 炸）。


def create_agent_session(title=""):
    """创建一条 Agent 会话，返回自增 id；失败返回 0"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO agent_sessions (title, summary) VALUES (?, ?)",
            (str(title or ""), "")
        )
        conn.commit()
        return int(cursor.lastrowid)
    except Exception as e:
        print("创建 Agent 会话失败：{}".format(e))
        return 0
    finally:
        conn.close()


def add_agent_message(session_id, role, content):
    """写入一条 Agent 会话消息（role: user / assistant / tool）"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO agent_messages (session_id, role, content) VALUES (?, ?, ?)",
            (int(session_id), str(role), str(content or ""))
        )
        conn.commit()
        return True
    except Exception as e:
        print("写入 Agent 消息失败：{}".format(e))
        return False
    finally:
        conn.close()


def get_agent_session(session_id):
    """按 id 取单条 Agent 会话，不存在返回 None"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM agent_sessions WHERE id = ?", (int(session_id),))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print("读取 Agent 会话失败：{}".format(e))
        return None
    finally:
        conn.close()


def get_agent_messages(session_id, limit=None):
    """按 id 顺序读取会话消息；limit 为 None 返回全部"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        if limit:
            cursor.execute(
                "SELECT * FROM agent_messages WHERE session_id = ? ORDER BY id LIMIT ?",
                (int(session_id), int(limit))
            )
        else:
            cursor.execute(
                "SELECT * FROM agent_messages WHERE session_id = ? ORDER BY id",
                (int(session_id),)
            )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print("读取 Agent 消息失败：{}".format(e))
        return []
    finally:
        conn.close()


def count_agent_messages(session_id, role=None):
    """统计会话消息数；role 非空时只统计该角色"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        if role:
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM agent_messages WHERE session_id = ? AND role = ?",
                (int(session_id), str(role))
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM agent_messages WHERE session_id = ?",
                (int(session_id),)
            )
        row = cursor.fetchone()
        return int(row['cnt']) if row else 0
    except Exception as e:
        print("统计 Agent 消息失败：{}".format(e))
        return 0
    finally:
        conn.close()


def update_agent_session_summary(session_id, summary):
    """更新会话摘要，并刷新 updated_at（供"最近 N 条摘要"按更新倒序取）"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE agent_sessions SET summary = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(summary or ""), int(session_id))
        )
        conn.commit()
        return True
    except Exception as e:
        print("更新 Agent 会话摘要失败：{}".format(e))
        return False
    finally:
        conn.close()


def list_recent_agent_sessions(limit=3):
    """取最近 limit 条【已有摘要】的会话，按更新倒序（同秒按 id 倒序稳定排序）"""
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM agent_sessions "
            "WHERE summary IS NOT NULL AND TRIM(summary) != '' "
            "ORDER BY updated_at DESC, id DESC LIMIT ?",
            (int(limit),)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print("读取最近 Agent 会话失败：{}".format(e))
        return []
    finally:
        conn.close()
