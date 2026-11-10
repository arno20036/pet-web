"""Simulated card payments, supplements and refund tasks. No real payment provider is involved."""
import time
import uuid
from datetime import datetime

from flask import current_app

from pet_platform.constants import OrderStatus, PaymentStatus
from pet_platform.db import get_db
from pet_platform.services import ServiceError
from pet_platform.services.ledger_service import _apply_platform_balance
from pet_platform.utils.time import NOW_SQL


class PaymentFormError(ServiceError):
    """Problem with what was entered on the payment form (user goes back to that form)."""


# A supplement shows up as more than one '支付訂單 #<id>' payment row for the same order.
PAID_SUPPLEMENT_SQL = '''(
    SELECT COUNT(*)
    FROM account_transactions
    WHERE user_id = o.buyer_id
    AND description = '支付訂單 #' || o.id
    AND transaction_type = 'payment'
) > 1'''


def pay_order(order_id, buyer_id, form):
    """Simulate paying the outstanding amount of a confirmed order (full or supplement)."""
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE id = ? AND buyer_id = ?', (order_id, buyer_id)).fetchone()
    if not order:
        raise ServiceError('訂單不存在')
    order = dict(order)
    if order['status'] != OrderStatus.CONFIRMED:
        raise ServiceError('此訂單無法付款（狀態不正確）')
    if order.get('payment_status') == PaymentStatus.PAID:
        raise ServiceError('此訂單已付款', category='info')

    saved_method_id = form.get('saved_method_id')
    if saved_method_id:
        method = conn.execute('SELECT * FROM saved_payment_methods WHERE id = ? AND user_id = ?',
                              (saved_method_id, buyer_id)).fetchone()
        if not method:
            raise PaymentFormError('無效的付款方式')
        card_last4 = method['card_number_masked'][-4:]
    else:
        card_last4 = _charge_new_card(buyer_id, form)

    time.sleep(1)  # simulated processing delay

    payment_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    payment_amount = order['total_price'] - (order.get('paid_amount') or 0)
    conn.execute(f'''
        UPDATE orders
        SET payment_status = 'paid',
            payment_method = 'credit_card',
            payment_time = ?,
            card_last4 = ?,
            payment_confirmed = 0,
            paid_amount = total_price,
            updated_at = {NOW_SQL}
        WHERE id = ?
    ''', (payment_time, card_last4, order_id))
    conn.execute(f'''
        INSERT INTO account_transactions (user_id, amount, transaction_type, status, description, created_at)
        VALUES (?, ?, 'payment', 'completed', ?, {NOW_SQL})
    ''', (buyer_id, -payment_amount, f"支付訂單 #{order_id}"))
    conn.commit()
    # The platform balance is credited later, when an admin confirms the payment.


def _charge_new_card(buyer_id, form):
    """Validate a newly entered card (simulation) and optionally save it. Returns its last 4 digits."""
    card_number = form.get('card_number', '').replace(' ', '')
    expiry_month = form.get('expiry_month')
    expiry_year = form.get('expiry_year')
    cvv = form.get('cvv')
    cardholder_name = form.get('cardholder_name')

    if not all([card_number, expiry_month, expiry_year, cvv, cardholder_name]):
        raise PaymentFormError('請填寫完整的付款資訊')
    if len(card_number) < 13 or len(card_number) > 16:
        raise PaymentFormError('信用卡號碼格式不正確')

    card_last4 = card_number[-4:]
    if form.get('save_card'):
        try:
            get_db().execute(f'''
                INSERT INTO saved_payment_methods (user_id, card_number_masked, card_expiry, cardholder_name, payment_token, created_at)
                VALUES (?, ?, ?, ?, ?, {NOW_SQL})
            ''', (buyer_id, f"**** **** **** {card_last4}", f"{expiry_month}/{expiry_year}",
                  cardholder_name, str(uuid.uuid4())))
        except Exception:
            current_app.logger.exception('Failed to save card')
    return card_last4


# ---------------------------------------------------------------------------
# admin finance page (refunds & supplements)
# ---------------------------------------------------------------------------

def _supplement_tasks(conn, trip_date):
    query = f'''
        SELECT o.*, o.id as order_id,
               u.name as user_name, -- Buyer
               s.name as processor_name, -- Seller (as context) or null
               o.booking_date
        FROM orders o
        JOIN users u ON o.buyer_id = u.id
        JOIN users s ON o.seller_id = s.id
        JOIN listings l ON o.listing_id = l.id
        WHERE (
            (o.paid_amount IS NOT NULL AND o.paid_amount > 0 AND o.paid_amount < o.total_price) -- Underpaid (Need Supplement)
            OR (o.payment_confirmed = 0 AND o.payment_status = 'paid' AND {PAID_SUPPLEMENT_SQL}) -- Paid supplement awaiting confirmation
        )
        AND o.status NOT IN ('cancelled', 'rejected')
    '''
    params = []
    if trip_date:
        query += ' AND o.booking_date = ?'
        params.append(trip_date)
    tasks = []
    for row in conn.execute(query + ' ORDER BY o.booking_date ASC', params).fetchall():
        item = dict(row)
        item['type'] = 'supplement'
        item['diff_amount'] = item['total_price'] - (item['paid_amount'] or 0)
        tasks.append(item)
    return tasks


def _completed_supplement_tasks(conn, trip_date):
    query = f'''
        SELECT o.*, o.id as order_id,
               u.name as user_name,
               s.name as processor_name, -- Seller context
               o.booking_date
        FROM orders o
        JOIN users u ON o.buyer_id = u.id
        JOIN users s ON o.seller_id = s.id
        WHERE o.status NOT IN ('cancelled', 'rejected')
        AND o.paid_amount IS NOT NULL
        AND o.paid_amount >= o.total_price
        AND o.payment_confirmed = 1
        AND {PAID_SUPPLEMENT_SQL}
    '''
    params = []
    if trip_date:
        query += ' AND o.booking_date = ?'
        params.append(trip_date)
    tasks = []
    for row in conn.execute(query + ' ORDER BY o.updated_at DESC', params).fetchall():
        item = dict(row)
        item['type'] = 'supplement_completed'
        item['diff_amount'] = 0  # fully paid
        tasks.append(item)
    return tasks


def _refund_tasks(conn, status, trip_date):
    query = '''
        SELECT t.*, u.name as user_name, p.name as processor_name, o.booking_date
        FROM refund_tasks t
        JOIN users u ON t.user_id = u.id
        JOIN orders o ON t.order_id = o.id
        LEFT JOIN users p ON t.processed_by = p.id
        WHERE t.status = ?
    '''
    params = [status]
    if trip_date:
        query += ' AND o.booking_date = ?'
        params.append(trip_date)
    tasks = []
    for row in conn.execute(query + ' ORDER BY t.created_at DESC', params).fetchall():
        task = dict(row)
        task['type'] = 'refund'
        # Show the consumer's latest bank account
        account = conn.execute('SELECT * FROM user_bank_accounts WHERE user_id = ? ORDER BY created_at DESC LIMIT 1',
                               (task['user_id'],)).fetchone()
        if account:
            for key in ('bank_code', 'account_number', 'account_name', 'branch_code'):
                task[key] = account[key]
        tasks.append(task)
    return tasks


def list_finance_tasks(status, trip_date=None):
    """status: 'supplement', 'supplement_completed', or a refund_tasks status ('pending' / 'processed')."""
    conn = get_db()
    if status == 'supplement':
        return _supplement_tasks(conn, trip_date)
    if status == 'supplement_completed':
        return _completed_supplement_tasks(conn, trip_date)
    return _refund_tasks(conn, status, trip_date)


def get_refund_task(task_id):
    return get_db().execute('SELECT * FROM refund_tasks WHERE id = ?', (task_id,)).fetchone()


def process_refund_task(task, admin_id, source):
    """Mark a refund task done, reduce the order's paid amount and book the refund expense.

    All three changes are written in one commit. `source` only labels the log line.
    """
    conn = get_db()
    try:
        conn.execute(f'''
            UPDATE refund_tasks
            SET status = 'processed', processed_by = ?, processed_at = {NOW_SQL}
            WHERE id = ?
        ''', (admin_id, task['id']))
        # paid_amount must stay the NET amount paid, or later supplement calculations break.
        conn.execute(f'''
            UPDATE orders
            SET payment_status = 'refunded',
                paid_amount = MAX(0, IFNULL(paid_amount, 0) - ?),
                updated_at = {NOW_SQL}
            WHERE id = ?
        ''', (task['amount'], task['order_id']))
        current_app.logger.info('Admin refund (%s): %s for order %s', source, -task['amount'], task['order_id'])
        _apply_platform_balance(conn, -task['amount'], 'refund', f'訂單 #{task["order_id"]} 退款',
                                order_id=task['order_id'])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
