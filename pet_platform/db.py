"""Per-request SQLite connection stored on flask.g, closed automatically at teardown."""
import sqlite3

from flask import current_app, g

from pet_platform.utils.time import NOW_SQL


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(exc=None):
    # Uncommitted changes are discarded here, exactly like the old manual conn.close().
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)


# Platform Account System
def ensure_platform_tables():
    """Ensure platform account tables exist"""
    conn = get_db()
    # Create platform_account table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS platform_account (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            balance REAL DEFAULT 0.0,
            updated_at TEXT
        )
    ''')
    # Create platform_transactions table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS platform_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            related_order_id INTEGER,
            related_withdrawal_id INTEGER,
            description TEXT,
            created_at TEXT
        )
    ''')
    # Ensure at least one platform account record exists
    existing = conn.execute('SELECT * FROM platform_account LIMIT 1').fetchone()
    if not existing:
        conn.execute(f'''
            INSERT INTO platform_account (balance, updated_at)
            VALUES (0.0, {NOW_SQL})
        ''')
    conn.commit()


def ensure_reschedule_tables():
    """Ensure reschedule and refund tables exist"""
    conn = get_db()
    
    # Reschedule Requests Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reschedule_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            new_date TEXT NOT NULL,
            new_time TEXT NOT NULL,
            new_duration INTEGER NOT NULL,
            price_diff INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', -- pending, approved, rejected
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders (id)
        )
    ''')

    # Refund Tasks Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS refund_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, -- Consumer to refund
            order_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending', -- pending, processed
            processed_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (order_id) REFERENCES orders (id),
            FOREIGN KEY (processed_by) REFERENCES users (id)
        )
    ''')
    
    # User Bank Accounts Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_bank_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bank_code TEXT NOT NULL,
            branch_code TEXT,
            account_number TEXT NOT NULL,
            account_name TEXT NOT NULL,
            is_default BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    conn.commit()
