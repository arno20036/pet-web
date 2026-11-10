"""Admin pages under /admin/."""
import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from pet_platform.constants import OrderStatus, PaymentStatus, Role
from pet_platform.db import get_db
from pet_platform.decorators import role_required
from pet_platform.services.ledger_service import get_platform_account, update_platform_balance
from pet_platform.utils.time import NOW_SQL, now_utc8

bp = Blueprint('admin', __name__)


@bp.route('/admin/listing-review')
@role_required(Role.ADMIN)
def admin_listing_review():
    conn = get_db()
    listings = conn.execute('''
        SELECT l.*, u.name as trainer_name, u.email as trainer_email 
        FROM listings l
        JOIN users u ON l.trainer_id = u.id
        WHERE l.status = 'pending'
    ''').fetchall()
    
    return render_template('admin_listing_review_list.html', listings=listings, active_page='listing_review', title='刊登申請審核')

@bp.route('/admin/listing-modification-review')
@role_required(Role.ADMIN)
def admin_listing_modification_review():
    conn = get_db()
    listings = conn.execute('''
        SELECT l.*, u.name as trainer_name, u.email as trainer_email 
        FROM listings l
        JOIN users u ON l.trainer_id = u.id
        WHERE l.status = 'modification_pending'
    ''').fetchall()
    
    return render_template('admin_listing_review_list.html', listings=listings, active_page='listing_modification_review', title='刊登修改審核', is_modification=True)

@bp.route('/admin/listing-review/<int:listing_id>')
@role_required(Role.ADMIN)
def admin_listing_review_detail(listing_id):
    conn = get_db()
    listing = conn.execute('''
        SELECT l.*, u.name as trainer_name, u.nickname as trainer_nickname, u.email as trainer_email, u.phone as trainer_phone
        FROM listings l
        JOIN users u ON l.trainer_id = u.id
        WHERE l.id = ?
    ''', (listing_id,)).fetchone()
    
    if listing:
        # Fetch other images
        images = conn.execute('SELECT filename FROM listing_images WHERE listing_id = ?', (listing_id,)).fetchall()
        # Convert to list of filenames
        additional_images = [img['filename'] for img in images]
    else:
        additional_images = []

    if listing is None:
        flash('找不到該刊登', 'error')
        return redirect(url_for('admin.admin_listing_review'))
        
    return render_template('admin_listing_review_detail.html', listing=listing, images=additional_images, active_page='listing_review')

@bp.route('/admin/listing-review/<int:listing_id>/approve', methods=['POST'])
@role_required(Role.ADMIN)
def admin_listing_approve(listing_id):
    conn = get_db()
    conn.execute("UPDATE listings SET status = 'approved' WHERE id = ?", (listing_id,))
    conn.commit()
    
    flash('刊登已核准', 'success')
    return redirect(url_for('admin.admin_listing_review'))

@bp.route('/admin/listing-review/<int:listing_id>/reject', methods=['POST'])
@role_required(Role.ADMIN)
def admin_listing_reject(listing_id):
    conn = get_db()
    conn.execute("UPDATE listings SET status = 'rejected' WHERE id = ?", (listing_id,))
    conn.commit()
    
    flash('刊登已駁回', 'info')
    return redirect(url_for('admin.admin_listing_review'))

@bp.route('/admin/order-tracking')
@role_required(Role.ADMIN)
def admin_order_tracking():
    conn = get_db()
    orders = conn.execute('''
        SELECT o.*,
               buyer.name as buyer_name, buyer.email as buyer_email, buyer.phone as buyer_phone,
               seller.name as seller_name, seller.email as seller_email, seller.phone as seller_phone,
               l.pet_name, l.pet_type
        FROM orders o
        JOIN users buyer ON o.buyer_id = buyer.id
        JOIN users seller ON o.seller_id = seller.id
        JOIN listings l ON o.listing_id = l.id
        ORDER BY o.created_at DESC
    ''').fetchall()
    
    # Process orders for display
    orders_list = []
    for row in orders:
        order = dict(row)
        # Translate pet_type
        if order['pet_type'] == 'dog':
            order['pet_type'] = '狗'
        elif order['pet_type'] == 'cat':
            order['pet_type'] = '貓'
        
        # Translate status
        status_map = {
            OrderStatus.PENDING: '待接受',
            OrderStatus.CONFIRMED: '已確認',
            OrderStatus.IN_PROGRESS: '進行中',
            OrderStatus.COMPLETED: '已完成',
            OrderStatus.REJECTED: '已拒絕',
            OrderStatus.CANCELLED: '已取消'
        }
        order['status_text'] = status_map.get(order['status'], order['status'])
        
        # Translate payment_status
        payment_map = {
            PaymentStatus.UNPAID: '未付款',
            PaymentStatus.PAID: '已付款',
            PaymentStatus.REFUNDED: '已退款'
        }
        order['payment_status_text'] = payment_map.get(order.get('payment_status', PaymentStatus.UNPAID), '未付款')
        
        orders_list.append(order)
    
    
    return render_template('admin_order_tracking.html', 
                          title='查看訂單流程進度', 
                          active_page='order_tracking',
                          orders=orders_list)

@bp.route('/admin/order/<int:order_id>/confirm-payment', methods=['POST'])
@role_required(Role.ADMIN)
def admin_confirm_payment(order_id):
    conn = get_db()
    
    # Check if order exists and payment is made
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    
    if not order:
        flash('訂單不存在', 'error')
        return redirect(url_for('admin.admin_order_tracking'))
    
    order = dict(order)
    
    if order.get('payment_status') != PaymentStatus.PAID:
        flash('此訂單尚未付款', 'error')
        return redirect(url_for('admin.admin_order_tracking'))
    
    if order.get('payment_confirmed') == 1:
        flash('此訂單已確認收款', 'info')
        return redirect(url_for('admin.admin_order_tracking'))
    
    # Update payment confirmation
    confirm_time = now_utc8().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('''
        UPDATE orders 
        SET payment_confirmed = 1,
            updated_at = ?
        WHERE id = ?
    ''', (confirm_time, order_id))
    conn.commit()
    
    # Calculate Unconfirmed Amount (Total Price - Already Recorded Income)
    # This ensures we handle supplements correctly and avoid double counting

    # Sum up all 'income' transactions for this order
    existing_income_row = conn.execute('''
        SELECT SUM(amount) as total 
        FROM platform_transactions 
        WHERE related_order_id = ? AND type IN ('income', 'refund')
    ''', (order_id,)).fetchone()
    
    existing_income = existing_income_row['total'] if existing_income_row['total'] else 0
    amount_to_add = order['total_price'] - existing_income
    
    if amount_to_add > 0:
        current_app.logger.info('Admin confirmed payment: adding %s for order %s', amount_to_add, order_id)
        update_platform_balance(
            amount=amount_to_add,
            transaction_type='income',
            description=f'訂單 #{order_id} 款項確認',
            order_id=order_id
        )
        msg = f'已確認收款！並已入帳 {amount_to_add} 元至平台帳戶'
    else:
        msg = '已確認收款！（金額已全數入帳，無新增款項）'
    
    flash(msg, 'success')
    return redirect(url_for('admin.admin_order_tracking'))

@bp.route('/admin/complaints')
@role_required(Role.ADMIN)
def admin_complaints():
    return render_template('admin_generic.html', title='申訴查詢', active_page='complaints')

@bp.route('/admin/trainer-review')
@role_required(Role.ADMIN)
def admin_trainer_review():
    conn = get_db()
    applications = conn.execute('''
        SELECT ta.*, u.name, u.email 
        FROM trainer_applications ta 
        JOIN users u ON ta.user_id = u.id 
        WHERE ta.status = 'pending'
    ''').fetchall()
    
    return render_template('admin_trainer_review_list.html', applications=applications, active_page='trainer_review')

@bp.route('/admin/trainer-review/<int:app_id>')
@role_required(Role.ADMIN)
def admin_trainer_review_detail(app_id):
    conn = get_db()
    application = conn.execute('''
        SELECT ta.*, u.name, u.email, u.nickname, u.gender, u.dob, u.city, u.district, u.phone
        FROM trainer_applications ta 
        JOIN users u ON ta.user_id = u.id 
        WHERE ta.id = ?
    ''', (app_id,)).fetchone()
    
    if application is None:
        flash('找不到該申請', 'error')
        return redirect(url_for('admin.admin_trainer_review'))
        
    return render_template('admin_trainer_review_detail.html', app=application, active_page='trainer_review')

@bp.route('/admin/trainer-review/<int:app_id>/approve', methods=['POST'])
@role_required(Role.ADMIN)
def admin_approve_application(app_id):
    conn = get_db()
    # Get application to find user_id
    app_row = conn.execute('SELECT user_id FROM trainer_applications WHERE id = ?', (app_id,)).fetchone()
    
    if app_row:
        user_id = app_row['user_id']
        # Update application status
        conn.execute("UPDATE trainer_applications SET status = 'approved' WHERE id = ?", (app_id,))
        # Update user role
        conn.execute("UPDATE users SET role = 'trainer' WHERE id = ?", (user_id,))
        conn.commit()
        flash('已核准申請，該會員已升級為訓練師', 'success')
    else:
        flash('找不到該申請', 'error')
        
    return redirect(url_for('admin.admin_trainer_review'))

@bp.route('/admin/trainer-review/<int:app_id>/reject', methods=['POST'])
@role_required(Role.ADMIN)
def admin_reject_application(app_id):
    conn = get_db()
    conn.execute("UPDATE trainer_applications SET status = 'rejected' WHERE id = ?", (app_id,))
    conn.commit()
    
    flash('已駁回申請', 'info')
    return redirect(url_for('admin.admin_trainer_review'))

# Admin Financial Management (Refunds & Supplements)
@bp.route('/admin/refunds')
@role_required(Role.ADMIN)
def admin_refunds():
    trip_date = request.args.get('trip_date')
    status = request.args.get('status', 'supplement')  # Default to 'supplement'
    
    conn = get_db()
    
    tasks = []
    
    if status == 'supplement':
        # Logic for Supplement (Underpaid orders)
        query = '''
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
                OR 
                (
                    o.payment_confirmed = 0 
                    AND o.payment_status = 'paid' 
                    AND (
                        SELECT COUNT(*) 
                        FROM account_transactions 
                        WHERE user_id = o.buyer_id 
                        AND description = '支付訂單 #' || o.id 
                        AND transaction_type = 'payment'
                    ) > 1
                ) -- Paid Supplement (More than 1 payment txn)
            )
            AND o.status NOT IN ('cancelled', 'rejected')
        '''
        params = []
        if trip_date:
            query += ' AND o.booking_date = ?'
            params.append(trip_date)
            
        query += ' ORDER BY o.booking_date ASC'
        
        rows = conn.execute(query, params).fetchall()
        for row in rows:
            item = dict(row)
            item['type'] = 'supplement'
            item['diff_amount'] = item['total_price'] - (item['paid_amount'] or 0)
            tasks.append(item)
            
    elif status == 'supplement_completed':
        # Logic for Completed Supplements (Paid & Confirmed)
        query = '''
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
            AND (
                SELECT COUNT(*) 
                FROM account_transactions 
                WHERE user_id = o.buyer_id 
                AND description = '支付訂單 #' || o.id 
                AND transaction_type = 'payment'
            ) > 1
        '''
        params = []
        if trip_date:
            query += ' AND o.booking_date = ?'
            params.append(trip_date)
            
        query += ' ORDER BY o.updated_at DESC'
        
        rows = conn.execute(query, params).fetchall()
        for row in rows:
            item = dict(row)
            item['type'] = 'supplement_completed'
            item['diff_amount'] = 0 # Fully paid
            tasks.append(item)
            
    else:
        # Existing Logic for Refunds
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
            
        query += ' ORDER BY t.created_at DESC'
        
        tasks_rows = conn.execute(query, params).fetchall()
        
        for row in tasks_rows:
            task = dict(row)
            task['type'] = 'refund'
            # Fetch user's latest bank account
            account = conn.execute('SELECT * FROM user_bank_accounts WHERE user_id = ? ORDER BY created_at DESC LIMIT 1', (task['user_id'],)).fetchone()
            if account:
                task['bank_code'] = account['bank_code']
                task['account_number'] = account['account_number']
                task['account_name'] = account['account_name']
                task['branch_code'] = account['branch_code']  # Fetch branch code too
            
            tasks.append(task)
        
    
    return render_template('admin_refunds.html', tasks=tasks, active_page='refunds', trip_date=trip_date, status=status)

@bp.route('/admin/refunds/<int:task_id>/process', methods=['POST'])
@role_required(Role.ADMIN)
def admin_process_refund(task_id):
    conn = get_db()
    
    # Fetch task details for balance update
    task = conn.execute('SELECT * FROM refund_tasks WHERE id = ?', (task_id,)).fetchone()
    if not task:
        flash('找不到該退款任務', 'error')
        return redirect(url_for('admin.admin_refunds'))
        
    try:
        # Mark task as processed
        conn.execute(f'''
            UPDATE refund_tasks 
            SET status = 'processed', processed_by = ?, processed_at = {NOW_SQL}
            WHERE id = ?
        ''', (session['user_id'], task_id))
        
        # Mark order as refunded (Sync status) AND Reduce paid_amount
        # This is crucial for correct supplement calculation later: paid_amount should reflect NET payment.
        conn.execute(f'''
            UPDATE orders 
            SET payment_status = 'refunded', 
                paid_amount = MAX(0, IFNULL(paid_amount, 0) - ?),
                updated_at = {NOW_SQL} 
            WHERE id = ?
        ''', (task['amount'], task['order_id']))
        
        conn.commit()
        
        # Update Platform Balance (Refund Expense)
        current_app.logger.info('Admin refund (UI): %s for order %s', -task['amount'], task['order_id'])
        update_platform_balance(-task['amount'], 'refund', f'訂單 #{task["order_id"]} 退款', order_id=task['order_id'])

        flash('退款任務已標記為完成，並已從平台餘額扣除款項', 'success')
    except Exception as e:
        flash(f'處理失敗: {str(e)}', 'error')
        
    return redirect(url_for('admin.admin_refunds'))

@bp.route('/admin/member-management')
@role_required(Role.ADMIN)
def admin_member_management():
    query = request.args.get('q', '').strip()
    role_filter = request.args.get('role', 'all')
    
    conn = get_db()
    
    sql = 'SELECT * FROM users WHERE 1=1'
    params = []
    
    if query:
        sql += ' AND (name LIKE ? OR nickname LIKE ? OR email LIKE ?)'
        search = f"%{query}%"
        params.extend([search, search, search])
        
    if role_filter and role_filter != 'all':
        sql += ' AND role = ?'
        params.append(role_filter)
        
    sql += ' ORDER BY created_at DESC'
    
    users = conn.execute(sql, params).fetchall()
    
    
    return render_template('admin_member_list.html', 
                           users=users, 
                           search_query=query, 
                           current_role=role_filter,
                           active_page='member_management')

    
@bp.route('/admin/statistics')
@role_required(Role.ADMIN)
def admin_statistics_dashboard():
    conn = get_db()
    
    # Date Filter
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    # Default to past 30 days if not specified
    if not start_date:
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = datetime.now().strftime('%Y-%m-%d')
        
    # Append time for query
    query_start = f"{start_date} 00:00:00"
    query_end = f"{end_date} 23:59:59"
    
    # 1. Total Transaction Amount
    total_transaction_row = conn.execute('''
        SELECT SUM(total_price) as total 
        FROM orders 
        WHERE status = 'completed' AND created_at BETWEEN ? AND ?
    ''', (query_start, query_end)).fetchone()
    total_transaction_amount = total_transaction_row['total'] if total_transaction_row['total'] else 0
    
    # 2. Platform Net Revenue
    net_revenue_row = conn.execute('''
        SELECT SUM(amount) as net_revenue 
        FROM platform_transactions 
        WHERE created_at BETWEEN ? AND ?
    ''', (query_start, query_end)).fetchone()
    platform_net_revenue = net_revenue_row['net_revenue'] if net_revenue_row['net_revenue'] else 0
    
    # 3. Order Completion Rate & 4. Appeal Rate prep
    order_stats = conn.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
        FROM orders
        WHERE created_at BETWEEN ? AND ? AND status != 'inquiry'
    ''', (query_start, query_end)).fetchone()
    
    total_orders = order_stats['total']
    completed_orders = order_stats['completed'] if order_stats['completed'] else 0
    
    completion_rate = round((completed_orders / total_orders * 100), 1) if total_orders > 0 else 0
    
    # 4. Appeal Rate
    # Check if appeals exist
    try:
        appeals_count = conn.execute('''
            SELECT COUNT(DISTINCT order_id) 
            FROM order_appeals 
            WHERE created_at BETWEEN ? AND ?
        ''', (query_start, query_end)).fetchone()[0]
    except:
        appeals_count = 0
        
    appeal_rate = round((appeals_count / total_orders * 100), 2) if total_orders > 0 else 0
    
    # 5. Consumer Retention Rate
    # Buyers with > 1 order in period
    retention_stats = conn.execute('''
        SELECT buyer_id, COUNT(*) as count
        FROM orders
        WHERE created_at BETWEEN ? AND ? AND status != 'inquiry'
        GROUP BY buyer_id
        HAVING count > 1
    ''', (query_start, query_end)).fetchall()
    repeat_buyers_count = len(retention_stats)
    
    total_buyers_count_row = conn.execute('''
        SELECT COUNT(DISTINCT buyer_id)
        FROM orders
        WHERE created_at BETWEEN ? AND ? AND status != 'inquiry'
    ''', (query_start, query_end)).fetchone()
    total_buyers_count = total_buyers_count_row[0] if total_buyers_count_row else 0
    
    retention_rate = round((repeat_buyers_count / total_buyers_count * 100), 1) if total_buyers_count > 0 else 0
    
    # 6. Pet Types
    pet_type_stats = conn.execute('''
        SELECT l.pet_type, COUNT(*) as count
        FROM orders o
        JOIN listings l ON o.listing_id = l.id
        WHERE o.created_at BETWEEN ? AND ?
        GROUP BY l.pet_type
    ''', (query_start, query_end)).fetchall()
    pet_labels = []
    pet_map = {'cat': '貓', 'dog': '狗'}
    for row in pet_type_stats:
        p_type = row['pet_type']
        pet_labels.append(pet_map.get(p_type, p_type))
    pet_data = [row['count'] for row in pet_type_stats]
    
    # 7. Popular Time Slots (Ongoing Orders)
    time_raw_stats = conn.execute('''
        SELECT booking_time, duration
        FROM orders
        WHERE created_at BETWEEN ? AND ? AND status != 'cancelled' AND status != 'rejected'
    ''', (query_start, query_end)).fetchall()
    
    # Initialize 24-hour slots
    hourly_counts = {h: 0 for h in range(24)}
    
    for row in time_raw_stats:
        b_time = row['booking_time']
        duration = row['duration']
        
        if b_time and ':' in b_time and duration:
            try:
                # Extract starting hour
                start_hour = int(b_time.split(':')[0])
                
                # Increment count for each hour of duration using modulo 24 for overflow
                for i in range(duration):
                    current_hour = (start_hour + i) % 24
                    hourly_counts[current_hour] += 1
            except ValueError:
                continue
                
    time_labels = [f"{h:02d}:00" for h in range(24)]
    # Use 'ongoing_orders_data' instead of start/end
    ongoing_orders_data = [hourly_counts[h] for h in range(24)]
    
    # 8. Trainer Rankings
    trainer_stats = conn.execute('''
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
    ''', (query_start, query_end)).fetchall()
    
    # Appeals by trainer
    try:
        appeals_stats = conn.execute('''
            SELECT o.seller_id, COUNT(*) as appeal_count
            FROM order_appeals a
            JOIN orders o ON a.order_id = o.id
            WHERE a.created_at BETWEEN ? AND ?
            GROUP BY o.seller_id
        ''', (query_start, query_end)).fetchall()
        appeal_map = {row['seller_id']: row['appeal_count'] for row in appeals_stats}
    except:
        appeal_map = {}
    
    all_trainers = []
    for row in trainer_stats:
        uid = row['user_id']
        data = dict(row)
        data['appeal_count'] = appeal_map.get(uid, 0)
        # Handle None values from SUM
        data['revenue'] = data['revenue'] if data['revenue'] else 0
        all_trainers.append(data)
        
    rank_completed = sorted(all_trainers, key=lambda x: x['completed_count'], reverse=True)[:10]
    rank_revenue = sorted(all_trainers, key=lambda x: x['revenue'], reverse=True)[:10]
    rank_rejected = sorted(all_trainers, key=lambda x: x['rejected_count'], reverse=True)[:10]
    rank_appealed = sorted(all_trainers, key=lambda x: x['appeal_count'], reverse=True)[:10]
    
    
    return render_template('admin_statistics_dashboard.html', 
                           start_date=start_date, end_date=end_date,
                           total_amount=total_transaction_amount,
                           net_revenue=platform_net_revenue,
                           completion_rate=completion_rate,
                           appeal_rate=appeal_rate,
                           retention_rate=retention_rate,
                           # Raw counts for tooltip display
                           completed_orders=completed_orders,
                           total_orders=total_orders,
                           appeals_count=appeals_count,
                           repeat_buyers_count=repeat_buyers_count,
                           total_buyers_count=total_buyers_count,
                           pet_labels=pet_labels,
                           pet_data=pet_data,
                           time_labels=time_labels,
                           ongoing_orders_data=ongoing_orders_data,
                           rank_completed=rank_completed,
                           rank_revenue=rank_revenue,
                           rank_rejected=rank_rejected,
                           rank_appealed=rank_appealed,
                           active_page='statistics')

@bp.route('/admin/member-management/<int:user_id>')
@role_required(Role.ADMIN)
def admin_member_detail(user_id):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

    if user is None:
        flash('找不到該會員', 'error')
        return redirect(url_for('admin.admin_member_management'))
    
    # Fetch level history if user is a trainer
    level_history = []
    if user['role'] == Role.TRAINER:
        level_history = conn.execute('''
            SELECT * FROM trainer_level_history 
            WHERE trainer_id = ? 
            ORDER BY created_at DESC
        ''', (user_id,)).fetchall()

    return render_template('admin_member_detail.html', user=user, level_history=level_history, active_page='member_management')

@bp.route('/admin/member-management/<int:user_id>/edit', methods=('GET', 'POST'))
@role_required(Role.ADMIN)
def admin_member_edit(user_id):
    conn = get_db()
    if request.method == 'POST':
        email = request.form['email']
        name = request.form['name']
        nickname = request.form['nickname']
        gender = request.form['gender']
        dob = request.form['dob']
        phone = request.form['phone']
        city = request.form['city']
        district = request.form['district']
        role = request.form['role']
        
        try:
            conn.execute('''
                UPDATE users 
                SET email = ?, name = ?, nickname = ?, gender = ?, dob = ?, phone = ?, city = ?, district = ?, role = ?
                WHERE id = ?
            ''', (email, name, nickname, gender, dob, phone, city, district, role, user_id))
            conn.commit()
            flash('會員資料已更新', 'success')
            return redirect(url_for('admin.admin_member_detail', user_id=user_id))
        except sqlite3.IntegrityError:
             flash('Email 已被使用', 'error')
        except Exception as e:
            flash(f'更新失敗: {e}', 'error')

    # GET request
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    
    if user is None:
        return redirect(url_for('admin.admin_member_management'))
        
    return render_template('admin_member_edit.html', user=user, active_page='member_management')

@bp.route('/admin/member-management/<int:user_id>/delete', methods=['POST'])
@role_required(Role.ADMIN)
def admin_member_delete(user_id):
    conn = get_db()
    try:
        # SQLite foreign keys are not enforced here, so dependent rows are deleted by hand.
        conn.execute('DELETE FROM trainer_applications WHERE user_id = ?', (user_id,))
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        flash('會員已刪除', 'success')
    except Exception as e:
        flash(f'刪除失敗: {e}', 'error')

    return redirect(url_for('admin.admin_member_management'))

@bp.route('/admin/appeals')
@role_required(Role.ADMIN)
def admin_appeals():
    conn = get_db()
    appeals = conn.execute('''
        SELECT a.*, o.booking_date, o.booking_time, u.name as user_name, u.email as user_email
        FROM order_appeals a
        JOIN orders o ON a.order_id = o.id
        JOIN users u ON a.user_id = u.id
        ORDER BY a.created_at DESC
    ''').fetchall()
    
    return render_template('admin_appeals_list.html', appeals=appeals, active_page='appeals', title='申訴案件管理')

@bp.route('/admin/appeal/<int:appeal_id>', methods=('GET', 'POST'))
@role_required(Role.ADMIN)
def admin_appeal_detail(appeal_id):
    conn = get_db()
    
    if request.method == 'POST':
        action = request.form.get('action')
        message = request.form.get('admin_reply', '').strip()
        
        # Handle message sending (reply_only)
        if action == 'reply_only':
            if message:
                conn.execute(f'''
                    INSERT INTO appeal_messages (appeal_id, sender_id, sender_role, message, created_at)
                    VALUES (?, ?, 'admin', ?, {NOW_SQL})
                ''', (appeal_id, session['user_id'], message))
                conn.commit()
            flash('留言已送出', 'success')
            return redirect(url_for('admin.admin_appeal_detail', appeal_id=appeal_id))
        else:
            # Handle status change actions
            if action == 'resolve':
                status = 'resolved'
            elif action == 'close':
                status = 'closed'
            else:
                status = 'processing'
            
            # Insert message if provided with status change
            if message:
                conn.execute(f'''
                    INSERT INTO appeal_messages (appeal_id, sender_id, sender_role, message, created_at)
                    VALUES (?, ?, 'admin', ?, {NOW_SQL})
                ''', (appeal_id, session['user_id'], message))
                
            conn.execute(f'''
                UPDATE order_appeals 
                SET status = ?, updated_at = {NOW_SQL}
                WHERE id = ?
            ''', (status, appeal_id))
            conn.commit()
            flash('案件狀態已更新', 'success')
            return redirect(url_for('admin.admin_appeals'))
        
    appeal = conn.execute('''
        SELECT a.*, o.booking_date, o.booking_time, o.total_price, 
               u.name as user_name, u.email as user_email, u.phone as user_phone,
               s.name as seller_name, s.email as seller_email
        FROM order_appeals a
        JOIN orders o ON a.order_id = o.id
        JOIN users u ON a.user_id = u.id
        JOIN users s ON o.seller_id = s.id
        WHERE a.id = ?
    ''', (appeal_id,)).fetchone()
    
    if not appeal:
        return redirect(url_for('admin.admin_appeals'))
    
    # Fetch all messages
    messages = conn.execute('''
        SELECT m.*, u.name as sender_name, u.nickname as sender_nickname
        FROM appeal_messages m
        JOIN users u ON m.sender_id = u.id
        WHERE m.appeal_id = ?
        ORDER BY m.created_at ASC
    ''', (appeal_id,)).fetchall()

    return render_template('admin_appeal_detail.html', appeal=appeal, messages=messages, active_page='appeals', title='案件詳情')

@bp.route('/admin/platform-settings', methods=('GET', 'POST'))
@role_required(Role.ADMIN)
def admin_fee_settings():
    conn = get_db()
    
    if request.method == 'POST':
        try:
            commission_general = float(request.form.get('commission_general', 15.0))
            commission_advanced = float(request.form.get('commission_advanced', 10.0))
            commission_professional = float(request.form.get('commission_professional', 5.0))
            level_adv_threshold = float(request.form.get('level_adv_threshold', 4.0))
            level_pro_threshold = float(request.form.get('level_pro_threshold', 4.5))
            
            # Legacy field update just in case
            commission_rate = commission_general 
            
            conn.execute(f'''
                UPDATE platform_settings 
                SET commission_rate = ?, 
                    commission_general = ?, commission_advanced = ?, commission_professional = ?,
                    level_adv_threshold = ?, level_pro_threshold = ?,
                    updated_at = {NOW_SQL}
                WHERE id = 1
            ''', (commission_rate, commission_general, commission_advanced, commission_professional, 
                  level_adv_threshold, level_pro_threshold))
            conn.commit()
            flash('平台設定已更新', 'success')
        except ValueError:
            flash('請輸入有效的數值', 'error')
    
    settings = conn.execute('SELECT * FROM platform_settings LIMIT 1').fetchone()
    
    return render_template('admin_platform_settings.html',
                          title='平台相關設定',
                          active_page='fee_settings',
                          settings=dict(settings) if settings else {})

# Admin Withdrawal Management Routes
@bp.route('/admin/withdrawal-requests')
@role_required(Role.ADMIN)
def admin_withdrawal_requests():
    conn = get_db()
    
    # Get all withdrawal requests with user info
    requests = conn.execute('''
        SELECT w.*, u.name as trainer_name, u.nickname as trainer_nickname,
               t.balance as current_balance
        FROM withdrawal_requests w
        JOIN users u ON w.user_id = u.id
        LEFT JOIN trainer_accounts t ON w.user_id = t.user_id
        ORDER BY w.created_at DESC
    ''').fetchall()
    
    
    pending_requests = [dict(r) for r in requests if r['status'] == 'pending']
    history_requests = [dict(r) for r in requests if r['status'] != 'pending']
    
    # Get platform account balance
    platform_account = get_platform_account()
    
    return render_template('admin_withdrawal_requests.html',
                          title='餘額提領申請',
                          active_page='withdrawal_requests',
                          pending_requests=pending_requests,
                          history_requests=history_requests,
                          platform_balance=platform_account['balance'])

@bp.route('/admin/withdrawal/<int:request_id>/approve', methods=['POST'])
@role_required(Role.ADMIN)
def admin_approve_withdrawal(request_id):
    conn = get_db()
    
    # Get withdrawal request
    withdrawal = conn.execute('SELECT * FROM withdrawal_requests WHERE id = ?', 
                             (request_id,)).fetchone()
    
    if not withdrawal:
        flash('找不到該提領申請', 'error')
        return redirect(url_for('admin.admin_withdrawal_requests'))
    
    if withdrawal['status'] != 'pending':
        flash('此申請已處理過', 'error')
        return redirect(url_for('admin.admin_withdrawal_requests'))
    
    # Get trainer account
    account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?',
                          (withdrawal['user_id'],)).fetchone()
    
    if not account or account['balance'] < withdrawal['amount']:
        flash('訓練師餘額不足，無法核准提領', 'error')
        return redirect(url_for('admin.admin_withdrawal_requests'))
    
    try:
        # Update account balance
        new_balance = account['balance'] - withdrawal['amount']
        conn.execute(f'''
            UPDATE trainer_accounts 
            SET balance = ?, updated_at = {NOW_SQL}
            WHERE user_id = ?
        ''', (new_balance, withdrawal['user_id']))

        # Record transaction
        conn.execute(f'''
            INSERT INTO account_transactions 
            (user_id, amount, type, description, created_at)
            VALUES (?, ?, 'withdrawal', ?, {NOW_SQL})
        ''', (withdrawal['user_id'], -withdrawal['amount'], 
              f'提領申請 #{request_id} 已核准'))
        
        # Update withdrawal request status
        conn.execute(f'''
            UPDATE withdrawal_requests
            SET status = 'approved', 
                processed_at = {NOW_SQL},
                processed_by = ?
            WHERE id = ?
        ''', (session['user_id'], request_id))
        
        conn.commit()
        
        # Note: Platform balance is NOT deducted here because it was deducted when order was completed (Liability recognition)
        # We just record the withdrawal status change.
        
        flash(f'已核准提領申請', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'處理失敗: {str(e)}', 'error')
    
    return redirect(url_for('admin.admin_withdrawal_requests'))

@bp.route('/admin/withdrawal/<int:request_id>/reject', methods=['POST'])
@role_required(Role.ADMIN)
def admin_reject_withdrawal(request_id):
    conn = get_db()
    
    withdrawal = conn.execute('SELECT * FROM withdrawal_requests WHERE id = ?',
                             (request_id,)).fetchone()
    
    if not withdrawal:
        flash('找不到該提領申請', 'error')
        return redirect(url_for('admin.admin_withdrawal_requests'))
    
    if withdrawal['status'] != 'pending':
        flash('此申請已處理過', 'error')
        return redirect(url_for('admin.admin_withdrawal_requests'))
    
    admin_note = request.form.get('admin_note', '')
    
    conn.execute(f'''
        UPDATE withdrawal_requests
        SET status = 'rejected',
            admin_note = ?,
            processed_at = {NOW_SQL},
            processed_by = ?
        WHERE id = ?
    ''', (admin_note, session['user_id'], request_id))
    
    conn.commit()
    
    flash('已拒絕提領申請', 'info')
    return redirect(url_for('admin.admin_withdrawal_requests'))
