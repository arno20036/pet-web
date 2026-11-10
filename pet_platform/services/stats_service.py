"""Admin statistics dashboard metrics and sidebar badge counts."""
from datetime import datetime, timedelta

from pet_platform.constants import Role
from pet_platform.db import get_db
from pet_platform.services.payment_service import PAID_SUPPLEMENT_SQL

PET_TYPE_LABELS = {'cat': '貓', 'dog': '狗'}


def _pct(part, whole, digits):
    return round((part / whole * 100), digits) if whole > 0 else 0


def default_date_range(start_date, end_date):
    """Missing dates default to the last 30 days."""
    if not start_date:
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = datetime.now().strftime('%Y-%m-%d')
    return start_date, end_date


# ---------------------------------------------------------------------------
# one function per dashboard metric; all take the inclusive [start, end] timestamps
# ---------------------------------------------------------------------------

def total_transaction_amount(conn, start, end):
    row = conn.execute('''
        SELECT SUM(total_price) as total
        FROM orders
        WHERE status = 'completed' AND created_at BETWEEN ? AND ?
    ''', (start, end)).fetchone()
    return row['total'] if row['total'] else 0


def platform_net_revenue(conn, start, end):
    row = conn.execute('''
        SELECT SUM(amount) as net_revenue
        FROM platform_transactions
        WHERE created_at BETWEEN ? AND ?
    ''', (start, end)).fetchone()
    return row['net_revenue'] if row['net_revenue'] else 0


def order_completion(conn, start, end):
    """(completed_orders, total_orders, completion_rate %) excluding inquiry orders."""
    row = conn.execute('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
        FROM orders
        WHERE created_at BETWEEN ? AND ? AND status != 'inquiry'
    ''', (start, end)).fetchone()
    total = row['total']
    completed = row['completed'] if row['completed'] else 0
    return completed, total, _pct(completed, total, 1)


def appeals_count(conn, start, end):
    try:
        return conn.execute('''
            SELECT COUNT(DISTINCT order_id)
            FROM order_appeals
            WHERE created_at BETWEEN ? AND ?
        ''', (start, end)).fetchone()[0]
    except Exception:
        return 0


def consumer_retention(conn, start, end):
    """(repeat_buyers, total_buyers, retention_rate %): buyers with more than one order in the period."""
    repeat = len(conn.execute('''
        SELECT buyer_id, COUNT(*) as count
        FROM orders
        WHERE created_at BETWEEN ? AND ? AND status != 'inquiry'
        GROUP BY buyer_id
        HAVING count > 1
    ''', (start, end)).fetchall())
    row = conn.execute('''
        SELECT COUNT(DISTINCT buyer_id)
        FROM orders
        WHERE created_at BETWEEN ? AND ? AND status != 'inquiry'
    ''', (start, end)).fetchone()
    total = row[0] if row else 0
    return repeat, total, _pct(repeat, total, 1)


def pet_type_breakdown(conn, start, end):
    rows = conn.execute('''
        SELECT l.pet_type, COUNT(*) as count
        FROM orders o
        JOIN listings l ON o.listing_id = l.id
        WHERE o.created_at BETWEEN ? AND ?
        GROUP BY l.pet_type
    ''', (start, end)).fetchall()
    return [PET_TYPE_LABELS.get(r['pet_type'], r['pet_type']) for r in rows], [r['count'] for r in rows]


def hourly_load(conn, start, end):
    """Orders active in each hour of the day (a 3-hour booking counts in 3 hours)."""
    rows = conn.execute('''
        SELECT booking_time, duration
        FROM orders
        WHERE created_at BETWEEN ? AND ? AND status != 'cancelled' AND status != 'rejected'
    ''', (start, end)).fetchall()
    counts = {h: 0 for h in range(24)}
    for row in rows:
        b_time, duration = row['booking_time'], row['duration']
        if b_time and ':' in b_time and duration:
            try:
                start_hour = int(b_time.split(':')[0])
                for i in range(duration):
                    counts[(start_hour + i) % 24] += 1
            except ValueError:
                continue
    return [f"{h:02d}:00" for h in range(24)], [counts[h] for h in range(24)]


def trainer_rankings(conn, start, end):
    rows = conn.execute('''
        SELECT
            u.id as user_id,
            u.name as trainer_name,
            COUNT(*) as total_orders,
            SUM(CASE WHEN o.status = 'completed' THEN 1 ELSE 0 END) as completed_count,
            SUM(CASE WHEN o.status = 'completed' THEN o.total_price ELSE 0 END) as revenue,
            SUM(CASE WHEN o.status = 'rejected' THEN 1 ELSE 0 END) as rejected_count
        FROM orders o
        JOIN users u ON o.seller_id = u.id
        WHERE o.created_at BETWEEN ? AND ?
        GROUP BY o.seller_id
    ''', (start, end)).fetchall()
    try:
        appeal_rows = conn.execute('''
            SELECT o.seller_id, COUNT(*) as appeal_count
            FROM order_appeals a
            JOIN orders o ON a.order_id = o.id
            WHERE a.created_at BETWEEN ? AND ?
            GROUP BY o.seller_id
        ''', (start, end)).fetchall()
        appeal_map = {r['seller_id']: r['appeal_count'] for r in appeal_rows}
    except Exception:
        appeal_map = {}

    trainers = []
    for row in rows:
        data = dict(row)
        data['appeal_count'] = appeal_map.get(row['user_id'], 0)
        data['revenue'] = data['revenue'] if data['revenue'] else 0
        trainers.append(data)

    def top(key):
        return sorted(trainers, key=lambda x: x[key], reverse=True)[:10]

    return {'rank_completed': top('completed_count'), 'rank_revenue': top('revenue'),
            'rank_rejected': top('rejected_count'), 'rank_appealed': top('appeal_count')}


def dashboard(start_date, end_date):
    """Template context for admin_statistics_dashboard.html."""
    start_date, end_date = default_date_range(start_date, end_date)
    start, end = f"{start_date} 00:00:00", f"{end_date} 23:59:59"
    conn = get_db()

    completed_orders, total_orders, completion_rate = order_completion(conn, start, end)
    appeals = appeals_count(conn, start, end)
    repeat_buyers, total_buyers, retention_rate = consumer_retention(conn, start, end)
    pet_labels, pet_data = pet_type_breakdown(conn, start, end)
    time_labels, ongoing_orders_data = hourly_load(conn, start, end)

    return dict(
        start_date=start_date, end_date=end_date,
        total_amount=total_transaction_amount(conn, start, end),
        net_revenue=platform_net_revenue(conn, start, end),
        completion_rate=completion_rate,
        appeal_rate=_pct(appeals, total_orders, 2),
        retention_rate=retention_rate,
        # raw counts for tooltips
        completed_orders=completed_orders, total_orders=total_orders, appeals_count=appeals,
        repeat_buyers_count=repeat_buyers, total_buyers_count=total_buyers,
        pet_labels=pet_labels, pet_data=pet_data,
        time_labels=time_labels, ongoing_orders_data=ongoing_orders_data,
        **trainer_rankings(conn, start, end),
    )


# ---------------------------------------------------------------------------
# sidebar badges (one query per role)
# ---------------------------------------------------------------------------

def _n(condition):
    """Count rows matching `condition` inside an aggregate query (0 for an empty table)."""
    return f"COALESCE(SUM(CASE WHEN {condition} THEN 1 ELSE 0 END), 0)"


# "... AS pending, ... AS confirmed, ... AS in_progress"
_ORDER_STATUS_COUNTS = ', '.join(_n("status = '%s'" % s) + ' AS ' + s for s in ('pending', 'confirmed', 'in_progress'))

_ADMIN_COUNTS_SQL = f'''
    SELECT l.*, ta.*, w.*, r.*, o.*
    FROM (SELECT {_n("status = 'pending'")} AS pending_listings,
                 {_n("status = 'modification_pending'")} AS pending_modifications
          FROM listings) l,
         (SELECT COUNT(*) AS pending_trainers FROM trainer_applications WHERE status = 'pending') ta,
         (SELECT COUNT(*) AS pending_withdrawals FROM withdrawal_requests WHERE status = 'pending') w,
         (SELECT COUNT(*) AS refund_tasks FROM refund_tasks WHERE status = 'pending') r,
         (SELECT
              -- paid by buyer, not yet confirmed by admin (paid supplements match this too)
              {_n("o.payment_status = 'paid' AND (o.payment_confirmed IS NULL OR o.payment_confirmed = 0)")} AS pending_payments,
              -- buyer still owes a supplement
              {_n("o.paid_amount IS NOT NULL AND o.paid_amount > 0 AND o.paid_amount < o.total_price AND o.status NOT IN ('cancelled', 'rejected')")} AS underpaid,
              -- paid supplement waiting for confirmation (same rule as the admin refunds page)
              {_n(f"o.payment_confirmed = 0 AND o.payment_status = 'paid' AND {PAID_SUPPLEMENT_SQL} AND o.status NOT IN ('cancelled', 'rejected')")} AS paid_supplements
          FROM orders o) o
'''


def sidebar_counts(user_id, role):
    conn = get_db()
    if role == Role.TRAINER:
        c = conn.execute(f'SELECT {_ORDER_STATUS_COUNTS} FROM orders WHERE seller_id = ?', (user_id,)).fetchone()
        return {
            'trainer_pending_orders': c['pending'],
            'trainer_confirmed_orders': c['confirmed'],
            'trainer_calendar_count': c['confirmed'] + c['in_progress'],
            # Order Management badge: orders still waiting on the trainer
            'trainer_badge_count': c['pending'] + c['confirmed'],
        }
    if role == Role.ADMIN:
        c = conn.execute(_ADMIN_COUNTS_SQL).fetchone()
        supplements = c['underpaid'] + c['paid_supplements']
        return {
            'admin_pending_listings': c['pending_listings'],
            'admin_pending_trainers': c['pending_trainers'],
            'admin_pending_withdrawals': c['pending_withdrawals'],
            'admin_pending_payments': c['pending_payments'],
            'admin_pending_modifications': c['pending_modifications'],
            'admin_tab_refund_count': c['refund_tasks'],
            'admin_tab_supplement_count': supplements,
            'admin_pending_refunds': c['refund_tasks'] + supplements,
        }
    if role == Role.MEMBER:
        c = conn.execute(f'SELECT {_ORDER_STATUS_COUNTS} FROM orders WHERE buyer_id = ?', (user_id,)).fetchone()
        return {
            'member_pending_orders': c['pending'],
            'member_upcoming_orders': c['confirmed'] + c['in_progress'],
            'member_badge_count': c['pending'] + c['confirmed'] + c['in_progress'],
        }
    return {}
