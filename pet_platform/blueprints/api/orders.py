"""Order APIs: cancel, reschedule, admin refund tasks."""
from datetime import datetime, timedelta

from flask import current_app, request, session

from pet_platform.constants import OrderStatus, PaymentStatus, Role
from pet_platform.db import get_db
from pet_platform.decorators import login_required, role_required
from pet_platform.services.ledger_service import update_platform_balance
from pet_platform.utils.time import NOW_SQL

from pet_platform.blueprints.api import bp


@bp.route('/api/order/<int:order_id>/reschedule', methods=['POST'])
@login_required(json=True)
def api_reschedule_order(order_id):
    data = request.get_json()
    new_date = data.get('date')
    new_time = data.get('time')
    new_duration = int(data.get('duration', 1))
    
    if not all([new_date, new_time, new_duration]):
        return {'error': 'Missing fields'}, 400
        
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE id = ? AND buyer_id = ?', (order_id, session['user_id'])).fetchone()
    
    if not order:
        return {'error': 'Order not found'}, 404
        
    order = dict(order)
    
    # Check limit
    resch_count = conn.execute("SELECT COUNT(*) FROM reschedule_requests WHERE order_id = ? AND status = 'approved'", (order_id,)).fetchone()[0]
    if resch_count >= 2:
        return {'error': '此訂單已達到改期次數上限 (2次)'}, 400
    
    # Calculate price diff
    # Assume price_at_booking exists
    old_price = order['total_price']
    old_duration = order['duration'] or 1
    
    # Find unit price
    unit_price = old_price / old_duration if old_duration > 0 else old_price
    
    new_price = unit_price * new_duration
    price_diff = int(new_price - old_price)
    
    try:
        # Check if pending request exists
        existing = conn.execute('SELECT * FROM reschedule_requests WHERE order_id = ? AND status = "pending"', (order_id,)).fetchone()
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
        success = True
    except Exception:
        current_app.logger.exception('Reschedule error')
        success = False

    if success:
        return {'success': True}
    else:
        return {'error': 'Database error'}, 500

@bp.route('/api/order/<int:order_id>/reschedule/respond', methods=['POST'])
@role_required(Role.TRAINER, json=True, status=401)
def api_respond_reschedule(order_id):
    data = request.get_json()
    action = data.get('action') # 'approve' or 'reject'
    
    if action not in ['approve', 'reject']:
        return {'error': 'Invalid action'}, 400
        
    conn = get_db()
    # Verify ownership
    order = conn.execute('SELECT * FROM orders WHERE id = ? AND seller_id = ?', (order_id, session['user_id'])).fetchone()
    
    if not order:
        return {'error': 'Order not found'}, 404
        
    # Get pending request
    request_row = conn.execute('SELECT * FROM reschedule_requests WHERE order_id = ? AND status = "pending"', (order_id,)).fetchone()
    
    if not request_row:
        return {'error': 'No pending request'}, 404
    
    try:
        if action == 'reject':
            conn.execute('UPDATE reschedule_requests SET status = "rejected" WHERE id = ?', (request_row['id'],))
            msg = '已拒絕改期申請'
            
        elif action == 'approve':
            # Update Order
            new_date = request_row['new_date']
            new_time = request_row['new_time']
            new_duration = request_row['new_duration']
            price_diff = request_row['price_diff']
            
            conn.execute(f'''
                UPDATE orders 
                SET booking_date = ?, booking_time = ?, duration = ?, total_price = total_price + ?, updated_at = {NOW_SQL}
                WHERE id = ?
            ''', (new_date, new_time, new_duration, price_diff, order_id))
            
            conn.execute('UPDATE reschedule_requests SET status = "approved" WHERE id = ?', (request_row['id'],))
            
            # Handle Price Diff (Refund if negative)
            if price_diff < 0:
                refund_amount = abs(price_diff)
                conn.execute(f'''
                    INSERT INTO refund_tasks (user_id, order_id, amount, reason, status, created_at)
                    VALUES (?, ?, ?, '改期退款 (時數減少/變更)', 'pending', {NOW_SQL})
                ''', (order['buyer_id'], order_id, refund_amount))
            elif price_diff > 0:
                # Require supplementary payment
                conn.execute('''
                    UPDATE orders 
                    SET payment_status = 'unpaid', payment_confirmed = 0 
                    WHERE id = ?
                ''', (order_id,))
                
            msg = '已同意改期申請'
            
        conn.commit()
        success = True
    except Exception as e:
        current_app.logger.exception('Reschedule response error')
        conn.rollback()
        success = False
        msg = f'操作失敗: {str(e)}'

    if success:
        return {'success': True, 'message': msg}
    else:
        return {'error': msg}, 500

# Order Cancellation API
@bp.route('/api/order/<int:order_id>/cancel', methods=['POST'])
@login_required(json=True)
def api_cancel_order(order_id):
    conn = get_db()
    try:
        order_row = conn.execute('''
            SELECT o.*, l.cancellation_hours, l.price, l.id as listing_id
            FROM orders o 
            JOIN listings l ON o.listing_id = l.id
            WHERE o.id = ? AND o.buyer_id = ?
        ''', (order_id, session['user_id'])).fetchone()
        
        if not order_row:
            return {'error': 'Order not found'}, 404
            
        order = dict(order_row)
            
        if order['status'] not in [OrderStatus.PENDING, OrderStatus.CONFIRMED]:
            return {'error': 'Cannot cancel order in current status'}, 400
            
        # Check Monthly Cancellation Limit
        now = datetime.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        cancel_count = conn.execute('''
            SELECT COUNT(*) FROM orders 
            WHERE buyer_id = ? AND status = 'cancelled' AND updated_at >= ?
        ''', (session['user_id'], start_of_month)).fetchone()[0]
        
        if cancel_count >= 2:
             return {'error': f'本月取消次數已達上限 (2次)。您本月已取消 {cancel_count} 次。'}, 400

        # Check Cancellation Deadline
        try:
             # Assume booking_time is "HH:MM" or "HH:MM:SS"
             booking_dt_str = f"{order['booking_date']} {order['booking_time']}"
             # Determine format based on length or colon count
             if len(order['booking_time']) > 5:
                  fmt = '%Y-%m-%d %H:%M:%S'
             else:
                  fmt = '%Y-%m-%d %H:%M'
                  
             booking_dt = datetime.strptime(booking_dt_str, fmt)
        except Exception as e:
            # Fallback for "14:00 - 16:00" style values; if that fails too, the deadline
            # check is skipped (cancellation allowed) rather than blocking the consumer.
            try:
                start_part = order['booking_time'].split('-')[0].strip()
                booking_dt = datetime.strptime(f"{order['booking_date']} {start_part}", '%Y-%m-%d %H:%M')
            except:
                current_app.logger.warning('Date parse error: %s', e)
                booking_dt = datetime.now() + timedelta(days=999) # Future

        deadline_hours = order['cancellation_hours'] if order['cancellation_hours'] is not None else 24
        deadline_dt = booking_dt - timedelta(hours=deadline_hours)
        
        if datetime.now() > deadline_dt:
            return {'error': f'已超過取消時限（需於 {deadline_hours} 小時前取消）'}, 400
            
        # Handle Refund if Paid
        data = request.get_json() or {}
        
        if order.get('payment_status') == PaymentStatus.PAID:
            bank_account_id = data.get('bank_account_id')
            new_bank_details = data.get('bank_details')
            
            refund_details = ""
            
            if bank_account_id:
                 acct = conn.execute('SELECT * FROM user_bank_accounts WHERE id = ?', (bank_account_id,)).fetchone()
                 if acct:
                     refund_details = f"銀行: {acct['bank_code']} 帳號: {acct['account_number']} 戶名: {acct['account_name']}"
            elif new_bank_details:
                 refund_details = f"銀行: {new_bank_details.get('bank_code')} 帳號: {new_bank_details.get('account_number')} 戶名: {new_bank_details.get('account_name')}"
                 
            if not refund_details:
                 return {'error': '退款資訊未提供'}, 400
                 
            # Update paid_amount
            conn.execute('UPDATE orders SET paid_amount = IFNULL(paid_amount, 0) + ? WHERE id = ?', (order['total_price'], order_id))

            # Create Refund Task
            # Calculate amount if missing
            refund_amount = order['total_price']
            if not refund_amount:
                price = order['price'] or 0
                duration = order['duration'] or 1
                refund_amount = price * duration
            
            conn.execute(f'''
                INSERT INTO refund_tasks (user_id, order_id, amount, reason, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', {NOW_SQL})
            ''', (session['user_id'], order_id, refund_amount, f"消費者取消: {refund_details}"))
            
        # Update Order
        conn.execute(f"UPDATE orders SET status = 'cancelled', updated_at = {NOW_SQL} WHERE id = ?", (order_id,))
        conn.commit()
        return {'success': True}

    except Exception as e:
        conn.rollback()
        current_app.logger.exception('Cancel error')
        return {'error': f'System Error: {str(e)}'}, 500

# Admin Refund APIs
@bp.route('/api/admin/refund-task/<int:order_id>', methods=['GET'])
@role_required(Role.ADMIN, json=True)
def get_admin_refund_task(order_id):
    conn = get_db()
    task = conn.execute('SELECT * FROM refund_tasks WHERE order_id = ? ORDER BY created_at DESC LIMIT 1', (order_id,)).fetchone()
    
    if not task:
        return {'error': 'Refund task not found'}, 404
        
    return {'task': dict(task)}

@bp.route('/api/admin/refund-task/<int:task_id>/process', methods=['POST'])
@role_required(Role.ADMIN, json=True)
def process_admin_refund_task(task_id):
    conn = get_db()
    task = conn.execute('SELECT * FROM refund_tasks WHERE id = ?', (task_id,)).fetchone()
    if not task:
        return {'error': 'Task not found'}, 404
        
    try:
        # Mark task as processed
        conn.execute(f'''
            UPDATE refund_tasks 
            SET status = 'processed', 
                processed_by = ?, 
                processed_at = {NOW_SQL} 
            WHERE id = ?
        ''', (session['user_id'], task_id))
        
        # Mark order as refunded AND Reduce paid_amount
        conn.execute(f'''
            UPDATE orders 
            SET payment_status = 'refunded', 
                paid_amount = MAX(0, IFNULL(paid_amount, 0) - ?),
                updated_at = {NOW_SQL} 
            WHERE id = ?
        ''', (task['amount'], task['order_id']))

        conn.commit()

        # Update Platform Balance (refund expense)
        current_app.logger.info('Admin refund (API): %s for order %s', -task['amount'], task['order_id'])
        update_platform_balance(-task['amount'], 'refund', f'訂單 #{task["order_id"]} 退款', order_id=task['order_id'])

        return {'success': True}
    except Exception as e:
        conn.rollback()
        return {'error': str(e)}, 500
