"""Order lifecycle: booking, status transitions, cancellation, rescheduling and order lists."""
from datetime import date, datetime, timedelta

from flask import current_app

from pet_platform.constants import OrderStatus, PaymentStatus
from pet_platform.db import get_db
from pet_platform.services import ServiceError
from pet_platform.services.ledger_service import get_trainer_commission_rate, process_order_payment_to_trainer
from pet_platform.utils.time import NOW_SQL

# (actor, action) -> (statuses the order may currently be in, status it moves to)
TRANSITIONS = {
    ('trainer', 'accept'): ({OrderStatus.PENDING}, OrderStatus.CONFIRMED),
    ('trainer', 'reject'): ({OrderStatus.PENDING}, OrderStatus.REJECTED),
    ('trainer', 'start'): ({OrderStatus.CONFIRMED}, OrderStatus.IN_PROGRESS),  # also needs a confirmed payment
    ('trainer', 'complete'): ({OrderStatus.IN_PROGRESS}, OrderStatus.COMPLETED),
    ('member', 'cancel'): ({OrderStatus.PENDING, OrderStatus.CONFIRMED}, OrderStatus.CANCELLED),
}

TRAINER_ACTION_MESSAGES = {
    'accept': ('訂單已確認接受！', 'success'),
    'reject': ('訂單已拒絕', 'info'),
    'start': ('行程已開始！', 'success'),
    'complete': ('行程已完成！', 'success'),
}


class ScheduleConflict(ServiceError):
    """Accepting would overlap other accepted orders; the trainer can retry with force."""

    def __init__(self, conflict_info):
        super().__init__(conflict_info, category='warning')
        self.conflict_info = conflict_info


def apply_transition(order_id, actor, action, extra_sql='', extra_params=()):
    """Conditional status UPDATE. Returns False if the order was not in an allowed state.

    Does not commit. `extra_sql` lets callers set more columns in the same statement.
    """
    allowed, new_status = TRANSITIONS[(actor, action)]
    placeholders = ', '.join('?' for _ in allowed)
    cur = get_db().execute(
        f'UPDATE orders SET status = ?, updated_at = {NOW_SQL}{extra_sql} '
        f'WHERE id = ? AND status IN ({placeholders})',
        (new_status, *extra_params, order_id, *sorted(allowed)))
    return cur.rowcount == 1


# ---------------------------------------------------------------------------
# trainer actions
# ---------------------------------------------------------------------------

def _hour_range(booking_time, duration):
    start = int(booking_time.split(':')[0])
    return start, start + duration


def find_schedule_conflicts(order):
    """Other confirmed / in-progress orders of the same trainer that overlap this one."""
    try:
        order_start, order_end = _hour_range(order['booking_time'], order.get('duration', 1))
    except Exception:
        order_start, order_end = 0, 1

    others = get_db().execute('''
        SELECT o.*, l.pet_type, l.breed
        FROM orders o
        LEFT JOIN listings l ON o.listing_id = l.id
        WHERE o.seller_id = ?
        AND o.booking_date = ?
        AND o.id != ?
        AND o.status IN ('confirmed', 'in_progress')
    ''', (order['seller_id'], order['booking_date'], order['id'])).fetchall()

    conflicts = []
    for row in others:
        other = dict(row)
        try:
            other_start, other_end = _hour_range(other['booking_time'], other.get('duration', 1))
        except Exception:
            continue
        # [order_start, order_end) overlaps [other_start, other_end)
        if not (order_end <= other_start or order_start >= other_end):
            conflicts.append({
                'id': other['id'],
                'time': f"{other_start:02d}:00 - {other_end:02d}:00",
                'title': f"{other.get('pet_type', '寵物')} ({other.get('breed', '未分類')})",
            })
    return conflicts


def trainer_action(order_id, trainer_id, action, force=False):
    """accept / reject / start / complete. Returns (message, flash_category)."""
    if ('trainer', action) not in TRANSITIONS:
        raise ServiceError('無效的操作')

    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE id = ? AND seller_id = ?', (order_id, trainer_id)).fetchone()
    if not order:
        raise ServiceError('找不到該訂單或無權限')
    order = dict(order)

    allowed, _ = TRANSITIONS[('trainer', action)]
    if order['status'] not in allowed:
        raise ServiceError('訂單目前的狀態無法執行此操作')

    msg, category = TRAINER_ACTION_MESSAGES[action]

    if action == 'accept':
        conflicts = find_schedule_conflicts(order)
        if conflicts and not force:
            raise ScheduleConflict("、".join(f"訂單#{c['id']}（{c['time']}）" for c in conflicts))
        if conflicts:
            msg += '（已略過時段重疊警告）'
    elif action == 'start':
        if order.get('payment_status') != PaymentStatus.PAID:
            raise ServiceError('此訂單尚未付款，無法開始行程。')
        if order.get('payment_confirmed') != 1:
            raise ServiceError('此訂單尚未確認收款，無法開始行程。請等待管理員確認收款。')

    if not apply_transition(order_id, 'trainer', action):
        conn.rollback()
        raise ServiceError('訂單目前的狀態無法執行此操作')
    conn.commit()

    if action == 'complete':
        try:
            if process_order_payment_to_trainer(order_id):
                commission_rate = get_trainer_commission_rate(order['seller_id'])
                msg += f' 款項已發放至您的帳戶（扣除 {commission_rate}% 平台費用）。'
        except Exception:
            conn.rollback()
            current_app.logger.exception('Payout failed for order %s', order_id)
            raise ServiceError('行程已完成，但款項發放失敗，請聯絡管理員。')

    return msg, category


# ---------------------------------------------------------------------------
# booking
# ---------------------------------------------------------------------------

def create_booking(listing, buyer_id, booking_date, start_time, end_time, location_type, location_detail,
                   allowed_weekdays):
    """Validate and insert a pending order. Returns (duration, total_price).

    Validation problems raise ServiceError; database errors propagate.
    """
    # Weekday numbering matches JS getDay(): 0=Sun ... 6=Sat
    y, m, d = map(int, booking_date.split('-'))
    if int(date(y, m, d).strftime("%w")) not in allowed_weekdays:
        raise ServiceError(f'該日期 ({booking_date}) 非開放預約日')

    duration = int(end_time.split(':')[0]) - int(start_time.split(':')[0])
    if duration <= 0:
        raise ServiceError('結束時間必須晚於開始時間')

    total_price = listing['price'] * duration
    conn = get_db()
    conn.execute(f'''
        INSERT INTO orders (listing_id, seller_id, buyer_id, booking_date, booking_time, duration, location_type, location_detail, price_at_booking, total_price, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', {NOW_SQL}, {NOW_SQL})
    ''', (listing['id'], listing['trainer_id'], buyer_id, booking_date, start_time, duration,
          location_type, location_detail, listing['price'], total_price))
    conn.commit()
    return duration, total_price


# ---------------------------------------------------------------------------
# cancellation
# ---------------------------------------------------------------------------

def monthly_cancel_count(buyer_id):
    start_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return get_db().execute('''
        SELECT COUNT(*) FROM orders
        WHERE buyer_id = ? AND status = 'cancelled' AND updated_at >= ?
    ''', (buyer_id, start_of_month)).fetchone()[0]


def _booking_datetime(order):
    try:
        fmt = '%Y-%m-%d %H:%M:%S' if len(order['booking_time']) > 5 else '%Y-%m-%d %H:%M'
        return datetime.strptime(f"{order['booking_date']} {order['booking_time']}", fmt)
    except Exception as e:
        # Fallback for "14:00 - 16:00" style values; if that fails too, the deadline
        # check is skipped (cancellation allowed) rather than blocking the consumer.
        try:
            start_part = order['booking_time'].split('-')[0].strip()
            return datetime.strptime(f"{order['booking_date']} {start_part}", '%Y-%m-%d %H:%M')
        except Exception:
            current_app.logger.warning('Date parse error: %s', e)
            return datetime.now() + timedelta(days=999)


def _refund_details(buyer_id, data):
    bank_account_id = data.get('bank_account_id')
    new_bank_details = data.get('bank_details')
    if bank_account_id:
        acct = get_db().execute('SELECT * FROM user_bank_accounts WHERE id = ?', (bank_account_id,)).fetchone()
        if acct:
            return f"銀行: {acct['bank_code']} 帳號: {acct['account_number']} 戶名: {acct['account_name']}"
    elif new_bank_details:
        return (f"銀行: {new_bank_details.get('bank_code')} 帳號: {new_bank_details.get('account_number')} "
                f"戶名: {new_bank_details.get('account_name')}")
    return ''


def cancel(order_id, buyer_id, data):
    """Member cancels an order; a paid order gets a pending refund task. Commits on success."""
    conn = get_db()
    order = conn.execute('''
        SELECT o.*, l.cancellation_hours, l.price, l.id as listing_id
        FROM orders o
        JOIN listings l ON o.listing_id = l.id
        WHERE o.id = ? AND o.buyer_id = ?
    ''', (order_id, buyer_id)).fetchone()
    if not order:
        raise ServiceError('Order not found', status=404)
    order = dict(order)

    allowed, _ = TRANSITIONS[('member', 'cancel')]
    if order['status'] not in allowed:
        raise ServiceError('Cannot cancel order in current status')

    cancel_count = monthly_cancel_count(buyer_id)
    if cancel_count >= 2:
        raise ServiceError(f'本月取消次數已達上限 (2次)。您本月已取消 {cancel_count} 次。')

    deadline_hours = order['cancellation_hours'] if order['cancellation_hours'] is not None else 24
    if datetime.now() > _booking_datetime(order) - timedelta(hours=deadline_hours):
        raise ServiceError(f'已超過取消時限（需於 {deadline_hours} 小時前取消）')

    refund_details = None
    if order.get('payment_status') == PaymentStatus.PAID:
        refund_details = _refund_details(buyer_id, data)
        if not refund_details:
            raise ServiceError('退款資訊未提供')

    if not apply_transition(order_id, 'member', 'cancel'):
        raise ServiceError('Cannot cancel order in current status')

    if refund_details is not None:
        conn.execute('UPDATE orders SET paid_amount = IFNULL(paid_amount, 0) + ? WHERE id = ?',
                     (order['total_price'], order_id))
        refund_amount = order['total_price'] or (order['price'] or 0) * (order['duration'] or 1)
        conn.execute(f'''
            INSERT INTO refund_tasks (user_id, order_id, amount, reason, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', {NOW_SQL})
        ''', (buyer_id, order_id, refund_amount, f"消費者取消: {refund_details}"))
    conn.commit()


# ---------------------------------------------------------------------------
# rescheduling
# ---------------------------------------------------------------------------

MAX_RESCHEDULES = 2


def request_reschedule(order_id, buyer_id, new_date, new_time, new_duration):
    """Create or replace the pending reschedule request. Database errors propagate."""
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE id = ? AND buyer_id = ?', (order_id, buyer_id)).fetchone()
    if not order:
        raise ServiceError('Order not found', status=404)

    approved = conn.execute("SELECT COUNT(*) FROM reschedule_requests WHERE order_id = ? AND status = 'approved'",
                            (order_id,)).fetchone()[0]
    if approved >= MAX_RESCHEDULES:
        raise ServiceError('此訂單已達到改期次數上限 (2次)')

    old_price = order['total_price']
    old_duration = order['duration'] or 1
    unit_price = old_price / old_duration if old_duration > 0 else old_price
    price_diff = int(unit_price * new_duration - old_price)

    existing = conn.execute('SELECT * FROM reschedule_requests WHERE order_id = ? AND status = "pending"',
                            (order_id,)).fetchone()
    if existing:
        conn.execute('''
            UPDATE reschedule_requests
            SET new_date = ?, new_time = ?, new_duration = ?, price_diff = ?
            WHERE id = ?
        ''', (new_date, new_time, new_duration, price_diff, existing['id']))
    else:
        conn.execute('''
            INSERT INTO reschedule_requests (order_id, new_date, new_time, new_duration, price_diff, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        ''', (order_id, new_date, new_time, new_duration, price_diff))
    conn.commit()


def respond_reschedule(order_id, trainer_id, action):
    """Trainer approves / rejects the pending request. Returns the message. Database errors propagate."""
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE id = ? AND seller_id = ?', (order_id, trainer_id)).fetchone()
    if not order:
        raise ServiceError('Order not found', status=404)

    req = conn.execute('SELECT * FROM reschedule_requests WHERE order_id = ? AND status = "pending"',
                       (order_id,)).fetchone()
    if not req:
        raise ServiceError('No pending request', status=404)

    if action == 'reject':
        conn.execute('UPDATE reschedule_requests SET status = "rejected" WHERE id = ?', (req['id'],))
        conn.commit()
        return '已拒絕改期申請'

    price_diff = req['price_diff']
    conn.execute(f'''
        UPDATE orders
        SET booking_date = ?, booking_time = ?, duration = ?, total_price = total_price + ?, updated_at = {NOW_SQL}
        WHERE id = ?
    ''', (req['new_date'], req['new_time'], req['new_duration'], price_diff, order_id))
    conn.execute('UPDATE reschedule_requests SET status = "approved" WHERE id = ?', (req['id'],))

    if price_diff < 0:
        conn.execute(f'''
            INSERT INTO refund_tasks (user_id, order_id, amount, reason, status, created_at)
            VALUES (?, ?, ?, '改期退款 (時數減少/變更)', 'pending', {NOW_SQL})
        ''', (order['buyer_id'], order_id, abs(price_diff)))
    elif price_diff > 0:
        # A longer booking needs a supplementary payment
        conn.execute('''
            UPDATE orders
            SET payment_status = 'unpaid', payment_confirmed = 0
            WHERE id = ?
        ''', (order_id,))
    conn.commit()
    return '已同意改期申請'


# ---------------------------------------------------------------------------
# order lists
# ---------------------------------------------------------------------------

PET_TYPE_LABELS = {'dog': '狗', 'cat': '貓'}

# Per role: whose orders, and the SELECT/FROM showing the other party.
_LIST_ROLES = {
    'member': {
        'owner': 'o.buyer_id',
        'select': 'o.*, l.pet_name, l.pet_type, l.available_slots, u.name as trainer_name, u.nickname as trainer_nickname',
        'from': 'orders o LEFT JOIN listings l ON o.listing_id = l.id JOIN users u ON o.seller_id = u.id',
    },
    'trainer': {
        'owner': 'o.seller_id',
        'select': ('o.*, u.name as buyer_name, u.phone as buyer_phone, u.nickname as buyer_nickname, '
                   'u.email as buyer_email, u.avatar as buyer_avatar, l.pet_name, l.pet_type'),
        'from': 'orders o JOIN users u ON o.buyer_id = u.id LEFT JOIN listings l ON o.listing_id = l.id',
    },
}

# (role, scope) -> (extra WHERE condition, ORDER BY, join the order's appeal?)
# Inquiry (chat-only) orders never appear in these lists.
_LIST_SCOPES = {
    ('member', 'pending'): ("o.status = 'pending'", 'o.booking_date, o.booking_time', False),
    ('member', 'upcoming'): ("((o.status = 'confirmed' AND o.booking_date >= :today) OR o.status = 'in_progress')",
                             'o.booking_date, o.booking_time', True),
    ('member', 'history'): ("(o.status NOT IN ('confirmed', 'pending', 'in_progress', 'inquiry') "
                            "OR (o.status = 'confirmed' AND o.booking_date < :today))",
                            'o.booking_date DESC, o.booking_time DESC', True),
    ('trainer', 'pending'): ("o.status = 'pending'", 'o.created_at ASC', False),
    ('trainer', 'history'): ("o.status NOT IN ('pending', 'inquiry')", 'o.booking_date DESC, o.booking_time DESC', False),
}


def list_orders(user_id, role, scope, start_date=None, end_date=None):
    """Orders of one member/trainer for one tab of the orders page, optionally filtered by booking date."""
    who = _LIST_ROLES[role]
    condition, order_by, with_appeal = _LIST_SCOPES[(role, scope)]
    select, from_ = who['select'], who['from']
    if with_appeal:
        select += ', a.status as appeal_status, a.id as appeal_id'
        from_ += ' LEFT JOIN order_appeals a ON o.id = a.order_id'

    sql = f"SELECT {select} FROM {from_} WHERE {who['owner']} = :uid AND {condition}"
    params = {'uid': user_id, 'today': date.today().strftime('%Y-%m-%d')}
    if start_date:
        sql += ' AND o.booking_date >= :start'
        params['start'] = start_date
    if end_date:
        sql += ' AND o.booking_date <= :end'
        params['end'] = end_date
    return get_db().execute(f'{sql} ORDER BY {order_by}', params).fetchall()


def _time_range(o):
    start_h = int(o['booking_time'].split(':')[0])
    duration = o['duration'] if o['duration'] else 1
    return f"{start_h:02d}:00 - {start_h + duration:02d}:00", duration


def _decorate(conn, row, user_id):
    """Fields both roles need: has_reviewed, time_range, total_price fallback."""
    o = dict(row)
    o['has_reviewed'] = bool(conn.execute('SELECT 1 FROM reviews WHERE order_id = ? AND source_user_id = ?',
                                          (o['id'], user_id)).fetchone())
    o['time_range'], duration = _time_range(o)
    if not o['total_price']:
        o['total_price'] = (o['price_at_booking'] or 0) * duration
    return o


def _decorate_member_order(conn, row, user_id):
    o = _decorate(conn, row, user_id)
    if o.get('pet_type') in PET_TYPE_LABELS:
        o['pet_type'] = PET_TYPE_LABELS[o['pet_type']]

    resch_req = conn.execute('SELECT status FROM reschedule_requests WHERE order_id = ? ORDER BY created_at DESC LIMIT 1',
                             (o['id'],)).fetchone()
    o['reschedule_status'] = resch_req['status'] if resch_req else None
    o['reschedule_count'] = conn.execute(
        "SELECT COUNT(*) FROM reschedule_requests WHERE order_id = ? AND status = 'approved'", (o['id'],)).fetchone()[0]

    # Latest refund task (pending or processed), if any
    refund_task = conn.execute('SELECT amount, status FROM refund_tasks WHERE order_id = ? ORDER BY created_at DESC LIMIT 1',
                               (o['id'],)).fetchone()
    o['refund_amount'] = refund_task['amount'] if refund_task else 0
    o['refund_status'] = refund_task['status'] if refund_task else None

    if o.get('original_price') is None:
        o['original_price'] = o['total_price']
    return o


def _decorate_trainer_order(conn, row, user_id):
    o = _decorate(conn, row, user_id)
    if 'pet_type' in o:
        o['listing_pet_type'] = PET_TYPE_LABELS.get(o['pet_type'], o['pet_type'])
    reschedule = conn.execute('SELECT * FROM reschedule_requests WHERE order_id = ? AND status = "pending"',
                              (o['id'],)).fetchone()
    if reschedule:
        o['reschedule_request'] = dict(reschedule)
    return o


def list_member_orders(user_id, start_date=None, end_date=None):
    """pending / upcoming / history lists for the member orders page."""
    conn = get_db()
    return {f'{scope}_orders': [_decorate_member_order(conn, r, user_id)
                                for r in list_orders(user_id, 'member', scope, start_date, end_date)]
            for scope in ('pending', 'upcoming', 'history')}


def list_trainer_orders(user_id):
    """pending / accepted / ongoing / history lists for the trainer orders page."""
    conn = get_db()
    lists = {'pending_orders': [_decorate_trainer_order(conn, r, user_id) for r in list_orders(user_id, 'trainer', 'pending')],
             'accepted_orders': [], 'ongoing_orders': [], 'history_orders': []}
    for row in list_orders(user_id, 'trainer', 'history'):
        o = _decorate_trainer_order(conn, row, user_id)
        if o['status'] == OrderStatus.CONFIRMED:
            lists['accepted_orders'].append(o)
        elif o['status'] == OrderStatus.IN_PROGRESS:
            lists['ongoing_orders'].append(o)
        else:  # rejected, cancelled, completed
            lists['history_orders'].append(o)
    return lists
