"""Trainer payouts, commission, levels, withdrawals and the platform account."""
from pet_platform.constants import TrainerLevel
from pet_platform.db import get_db
from pet_platform.services import bank_account_service, ServiceError
from pet_platform.utils.time import NOW_SQL

DEFAULT_SETTINGS = {
    'commission_rate': 15.0,  # legacy column, mirrors commission_general
    'commission_general': 15.0,
    'commission_advanced': 10.0,
    'commission_professional': 5.0,
    'level_adv_threshold': 4.0,
    'level_pro_threshold': 4.5,
}


def get_platform_settings():
    settings = get_db().execute('SELECT * FROM platform_settings LIMIT 1').fetchone()
    return dict(settings) if settings else dict(DEFAULT_SETTINGS)


def commission_rate_for_level(level, settings=None):
    settings = settings or get_platform_settings()
    if level == TrainerLevel.PROFESSIONAL:
        return settings.get('commission_professional', 5.0)
    if level == TrainerLevel.ADVANCED:
        return settings.get('commission_advanced', 10.0)
    return settings.get('commission_general', 15.0)


def get_trainer_commission_rate(trainer_id):
    account = get_db().execute('SELECT current_level FROM trainer_accounts WHERE user_id = ?',
                               (trainer_id,)).fetchone()
    level = account['current_level'] if account and account['current_level'] else TrainerLevel.GENERAL
    return commission_rate_for_level(level)


def update_trainer_level(trainer_id):
    """Recompute the level from the average rating buyers gave this trainer."""
    conn = get_db()
    avg_row = conn.execute('''
        SELECT AVG(rating) as avg_rating
        FROM reviews
        WHERE target_user_id = ? AND review_type = 'buyer_to_trainer'
    ''', (trainer_id,)).fetchone()
    avg_rating = avg_row['avg_rating'] if avg_row and avg_row['avg_rating'] else 0

    settings = get_platform_settings()
    new_level = TrainerLevel.GENERAL
    if avg_rating >= settings.get('level_pro_threshold', 4.5):
        new_level = TrainerLevel.PROFESSIONAL
    elif avg_rating >= settings.get('level_adv_threshold', 4.0):
        new_level = TrainerLevel.ADVANCED

    current_level_row = conn.execute('SELECT current_level FROM trainer_accounts WHERE user_id = ?',
                                     (trainer_id,)).fetchone()
    current_level = current_level_row['current_level'] if current_level_row else TrainerLevel.GENERAL

    conn.execute('UPDATE trainer_accounts SET current_level = ? WHERE user_id = ?', (new_level, trainer_id))
    if current_level != new_level:
        conn.execute('''
            INSERT INTO trainer_level_history (trainer_id, old_level, new_level, trigger_source)
            VALUES (?, ?, ?, 'system_update')
        ''', (trainer_id, current_level, new_level))
    conn.commit()
    return new_level


# ---------------------------------------------------------------------------
# platform account
# ---------------------------------------------------------------------------

def get_platform_account():
    account = get_db().execute('SELECT * FROM platform_account LIMIT 1').fetchone()
    return dict(account) if account else {'id': 1, 'balance': 0.0}


def _apply_platform_balance(conn, amount, transaction_type, description, order_id=None, withdrawal_id=None):
    """Change the platform balance and record it. Does NOT commit: the caller owns the transaction."""
    account = conn.execute('SELECT * FROM platform_account LIMIT 1').fetchone()
    new_balance = account['balance'] + amount
    conn.execute(f'''
        UPDATE platform_account
        SET balance = ?, updated_at = {NOW_SQL}
        WHERE id = ?
    ''', (new_balance, account['id']))
    conn.execute(f'''
        INSERT INTO platform_transactions
        (type, amount, related_order_id, related_withdrawal_id, description, created_at)
        VALUES (?, ?, ?, ?, ?, {NOW_SQL})
    ''', (transaction_type, amount, order_id, withdrawal_id, description))
    return new_balance


def update_platform_balance(amount, transaction_type, description, order_id=None, withdrawal_id=None):
    """Update the platform balance in its own commit."""
    conn = get_db()
    new_balance = _apply_platform_balance(conn, amount, transaction_type, description, order_id, withdrawal_id)
    conn.commit()
    return new_balance


# ---------------------------------------------------------------------------
# trainer payouts
# ---------------------------------------------------------------------------

def process_order_payment_to_trainer(order_id):
    """Credit the trainer for a completed order. Returns False if already paid out.

    Trainer account, trainer ledger row and platform account change in ONE commit.
    """
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    if not order:
        return False
    order = dict(order)

    already_paid = conn.execute(
        'SELECT * FROM account_transactions WHERE order_id = ? AND type = "income"', (order_id,)
    ).fetchone()
    if already_paid:
        return False

    trainer_id = order['seller_id']
    commission_rate = get_trainer_commission_rate(trainer_id)
    original_amount = order['total_price']
    commission = round(original_amount * (commission_rate / 100), 2)
    trainer_income = round(original_amount - commission, 2)

    account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?', (trainer_id,)).fetchone()
    if not account:
        conn.execute(f'''
            INSERT INTO trainer_accounts (user_id, balance, total_earned, created_at, updated_at)
            VALUES (?, ?, ?, {NOW_SQL}, {NOW_SQL})
        ''', (trainer_id, trainer_income, trainer_income))
    else:
        conn.execute(f'''
            UPDATE trainer_accounts
            SET balance = ?, total_earned = ?, updated_at = {NOW_SQL}
            WHERE user_id = ?
        ''', (account['balance'] + trainer_income, account['total_earned'] + trainer_income, trainer_id))

    conn.execute(f'''
        INSERT INTO account_transactions
        (user_id, order_id, amount, commission, original_amount, type, description, created_at)
        VALUES (?, ?, ?, ?, ?, 'income', ?, {NOW_SQL})
    ''', (trainer_id, order_id, trainer_income, commission, original_amount, f'訂單 #{order_id} 完成收入'))

    # The platform recognises the trainer's share as a liability.
    _apply_platform_balance(conn, -trainer_income, 'payout_allocation',
                            f'訂單 #{order_id} 收益分配給訓練師', order_id=order_id)
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# trainer account page & withdrawals
# ---------------------------------------------------------------------------

def get_or_create_trainer_account(user_id):
    conn = get_db()
    account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?', (user_id,)).fetchone()
    if not account:
        conn.execute(f'''
            INSERT INTO trainer_accounts (user_id, balance, total_earned, created_at, updated_at)
            VALUES (?, 0.0, 0.0, {NOW_SQL}, {NOW_SQL})
        ''', (user_id,))
        conn.commit()
        account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?', (user_id,)).fetchone()
    return account


def trainer_transactions(user_id, filter_date=None):
    """Ledger rows newest first, each with balance_after.

    The running balance is computed over ALL rows, then the date filter is applied.
    """
    rows = get_db().execute('''
        SELECT t.*, o.booking_date, o.booking_time
        FROM account_transactions t
        LEFT JOIN orders o ON t.order_id = o.id
        WHERE t.user_id = ?
        ORDER BY t.created_at ASC
    ''', (user_id,)).fetchall()

    transactions = []
    running = 0.0
    for row in rows:
        t = dict(row)
        running += t['amount']
        t['balance_after'] = running
        transactions.append(t)
    transactions.reverse()

    if filter_date:
        transactions = [t for t in transactions if t['created_at'].startswith(filter_date)]
    return transactions


def withdrawal_requests_for(user_id):
    rows = get_db().execute('''
        SELECT * FROM withdrawal_requests
        WHERE user_id = ?
        ORDER BY created_at DESC
    ''', (user_id,)).fetchall()
    return [dict(r) for r in rows]


def request_withdrawal(user_id, balance, amount_raw, bank_code, bank_account, account_holder, save_account):
    """Validate and create a pending withdrawal request (optionally saving the bank account)."""
    try:
        amount = float(amount_raw)
    except ValueError:
        raise ServiceError('請輸入有效的金額')

    if amount <= 0:
        raise ServiceError('提領金額必須大於 0')
    if amount > balance:
        raise ServiceError('提領金額不可超過帳戶餘額')
    if not bank_code or not bank_account or not account_holder:
        raise ServiceError('請填寫完整銀行資訊')
    if not bank_code.isdigit() or len(bank_code) != 3:
        raise ServiceError('銀行代碼必須為 3 位數字')

    conn = get_db()
    if save_account:
        bank_account_service.remember_trainer_account(user_id, bank_code, bank_account, account_holder)

    conn.execute(f'''
        INSERT INTO withdrawal_requests
        (user_id, amount, bank_name, bank_account, account_holder, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', {NOW_SQL})
    ''', (user_id, amount, bank_code, bank_account, account_holder))
    conn.commit()
