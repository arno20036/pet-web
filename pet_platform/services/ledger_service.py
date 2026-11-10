"""Trainer payouts, commission, levels and the platform account (moved unchanged from app.py)."""
from pet_platform.constants import TrainerLevel
from pet_platform.db import ensure_platform_tables, get_db
from pet_platform.utils.time import NOW_SQL


# Helper functions for trainer account system
def get_platform_settings():
    conn = get_db()
    settings = conn.execute('SELECT * FROM platform_settings LIMIT 1').fetchone()
    if settings:
        return dict(settings)
    return {
        'commission_rate': 15.0, # Legacy fallback
        'commission_general': 15.0,
        'commission_advanced': 10.0,
        'commission_professional': 5.0,
        'level_adv_threshold': 4.0,
        'level_pro_threshold': 4.5
    }


def get_trainer_commission_rate(trainer_id):
    conn = get_db()
    account = conn.execute('SELECT current_level FROM trainer_accounts WHERE user_id = ?', (trainer_id,)).fetchone()
    
    settings = get_platform_settings()
    
    level = account['current_level'] if account and account['current_level'] else TrainerLevel.GENERAL
    
    if level == TrainerLevel.PROFESSIONAL:
        return settings.get('commission_professional', 5.0)
    elif level == TrainerLevel.ADVANCED:
        return settings.get('commission_advanced', 10.0)
    else:
        # Default or general
        return settings.get('commission_general', 15.0)


def update_trainer_level(trainer_id):
    conn = get_db()
    # Level is based on the average of reviews buyers gave this trainer
    # (target_user_id is the person being reviewed).
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
        
    # Get current level
    current_level_row = conn.execute('SELECT current_level FROM trainer_accounts WHERE user_id = ?', (trainer_id,)).fetchone()
    current_level = current_level_row['current_level'] if current_level_row else TrainerLevel.GENERAL
        
    conn.execute('UPDATE trainer_accounts SET current_level = ? WHERE user_id = ?', (new_level, trainer_id))
    
    # Record history if level changed
    if current_level != new_level:
        conn.execute('''
            INSERT INTO trainer_level_history (trainer_id, old_level, new_level, trigger_source)
            VALUES (?, ?, ?, 'system_update')
        ''', (trainer_id, current_level, new_level))
        
    conn.commit()
    return new_level


def get_platform_account():
    """Get platform account balance"""
    ensure_platform_tables()
    conn = get_db()
    account = conn.execute('SELECT * FROM platform_account LIMIT 1').fetchone()
    return dict(account) if account else {'id': 1, 'balance': 0.0}


def update_platform_balance(amount, transaction_type, description, order_id=None, withdrawal_id=None):
    """Update platform account balance and record transaction"""
    ensure_platform_tables()
    conn = get_db()
    
    # Get current balance
    account = conn.execute('SELECT * FROM platform_account LIMIT 1').fetchone()
    new_balance = account['balance'] + amount
    
    # Update balance
    conn.execute(f'''
        UPDATE platform_account 
        SET balance = ?, updated_at = {NOW_SQL}
        WHERE id = ?
    ''', (new_balance, account['id']))
    
    # Record transaction
    conn.execute(f'''
        INSERT INTO platform_transactions 
        (type, amount, related_order_id, related_withdrawal_id, description, created_at)
        VALUES (?, ?, ?, ?, ?, {NOW_SQL})
    ''', (transaction_type, amount, order_id, withdrawal_id, description))
    
    conn.commit()
    return new_balance


def process_order_payment_to_trainer(order_id):
    """Process payment to trainer when order is completed"""
    conn = get_db()
    
    # Get order details
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    if not order:
        return False
    
    order = dict(order)
    
    # Check if payment already processed
    existing_transaction = conn.execute(
        'SELECT * FROM account_transactions WHERE order_id = ? AND type = "income"',
        (order_id,)
    ).fetchone()
    
    if existing_transaction:
        return False  # Already processed
    
    # Calculate amounts
    trainer_id = order['seller_id']
    commission_rate = get_trainer_commission_rate(trainer_id)
    original_amount = order['total_price']
    commission = round(original_amount * (commission_rate / 100), 2)
    trainer_income = round(original_amount - commission, 2)
    
    # Get or create trainer account
    account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?', (trainer_id,)).fetchone()
    
    if not account:
        conn.execute(f'''
            INSERT INTO trainer_accounts (user_id, balance, total_earned, created_at, updated_at)
            VALUES (?, ?, ?, {NOW_SQL}, {NOW_SQL})
        ''', (trainer_id, trainer_income, trainer_income))
    else:
        new_balance = account['balance'] + trainer_income
        new_total = account['total_earned'] + trainer_income
        conn.execute(f'''
            UPDATE trainer_accounts
            SET balance = ?, total_earned = ?, updated_at = {NOW_SQL}
            WHERE user_id = ?
        ''', (new_balance, new_total, trainer_id))
    
    # Record transaction
    conn.execute(f'''
        INSERT INTO account_transactions 
        (user_id, order_id, amount, commission, original_amount, type, description, created_at)
        VALUES (?, ?, ?, ?, ?, 'income', ?, {NOW_SQL})
    ''', (trainer_id, order_id, trainer_income, commission, original_amount, 
          f'訂單 #{order_id} 完成收入'))
    
    conn.commit()
    
    # Deduct trainer income from platform account (Recognize liability)
    update_platform_balance(
        amount=-trainer_income,
        transaction_type='payout_allocation',
        description=f'訂單 #{order_id} 收益分配給訓練師',
        order_id=order_id
    )
    
    return True
