"""Bank accounts. Members (refunds) and trainers (withdrawals) keep separate tables for now."""
from pet_platform.db import get_db
from pet_platform.utils.time import NOW_SQL

TABLES = {'member': 'user_bank_accounts', 'trainer': 'trainer_bank_accounts'}


def list_accounts(owner, user_id, default_first=False):
    order = 'is_default DESC, created_at DESC' if default_first else 'created_at DESC'
    rows = get_db().execute(f'SELECT * FROM {TABLES[owner]} WHERE user_id = ? ORDER BY {order}', (user_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_account(owner, account_id, user_id):
    """Only deletes the user's own account; unknown ids are silently ignored."""
    conn = get_db()
    conn.execute(f'DELETE FROM {TABLES[owner]} WHERE id = ? AND user_id = ?', (account_id, user_id))
    conn.commit()


def add_member_account(user_id, bank_code, account_number, account_name, branch_code=None, is_default=False):
    conn = get_db()
    if is_default:
        conn.execute('UPDATE user_bank_accounts SET is_default = 0 WHERE user_id = ?', (user_id,))
    conn.execute('''
        INSERT INTO user_bank_accounts (user_id, bank_code, branch_code, account_number, account_name, is_default)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, bank_code, branch_code, account_number, account_name, is_default))
    conn.commit()


def remember_trainer_account(user_id, bank_code, bank_account, account_holder):
    """Save a trainer's withdrawal account unless it is already saved. Does not commit."""
    conn = get_db()
    existing = conn.execute('''
        SELECT id FROM trainer_bank_accounts
        WHERE user_id = ? AND bank_code = ? AND bank_account = ?
    ''', (user_id, bank_code, bank_account)).fetchone()
    if not existing:
        conn.execute(f'''
            INSERT INTO trainer_bank_accounts (user_id, bank_code, bank_account, account_holder, created_at)
            VALUES (?, ?, ?, ?, {NOW_SQL})
        ''', (user_id, bank_code, bank_account, account_holder))
