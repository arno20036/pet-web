"""Member (consumer) pages under /member/."""
import time
import uuid
from datetime import date, datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from pet_platform.constants import OrderStatus, PaymentStatus
from pet_platform.db import get_db
from pet_platform.decorators import login_required
from pet_platform.utils.time import NOW_SQL
from pet_platform.utils.uploads import file_extension, save_upload

bp = Blueprint('member', __name__)


@bp.route('/member/bank_account', methods=['POST'])
@login_required
def add_bank_account():
    bank_code = request.form.get('bank_code')
    branch_code = request.form.get('branch_code')
    account_number = request.form.get('account_number')
    account_name = request.form.get('account_name')
    
    if not all([bank_code, account_number, account_name]):
        flash('請填寫完整銀行帳戶資訊', 'error')
        return redirect(url_for('member.member_profile'))
        
    conn = get_db()
    conn.execute('''
        INSERT INTO user_bank_accounts (user_id, bank_code, branch_code, account_number, account_name)
        VALUES (?, ?, ?, ?, ?)
    ''', (session['user_id'], bank_code, branch_code, account_number, account_name))
    conn.commit()
    
    flash('銀行帳戶已新增', 'success')
    return redirect(url_for('member.member_profile'))

@bp.route('/member/bank_account/<int:account_id>/delete', methods=['POST'])
@login_required
def delete_user_bank_account(account_id):
    conn = get_db()
    conn.execute('DELETE FROM user_bank_accounts WHERE id = ? AND user_id = ?', (account_id, session['user_id']))
    conn.commit()
    
    flash('銀行帳戶已刪除', 'success')
    return redirect(url_for('member.member_profile'))

@bp.route('/member/profile', methods=('GET', 'POST'))
@login_required
def member_profile():
    conn = get_db()
    
    if request.method == 'POST':
        nickname = request.form['nickname']
        phone = request.form['phone']
        city = request.form['city']
        district = request.form['district']
        
        avatar_file = request.files.get('avatar')
        delete_avatar = request.form.get('delete_avatar')

        try:
            # Handle Avatar Deletion
            if delete_avatar == 'true':
                conn.execute('UPDATE users SET avatar = NULL WHERE id = ?', (session['user_id'],))
            
            # Handle Avatar Upload
            if avatar_file and avatar_file.filename:
                if file_extension(avatar_file.filename) in {'png', 'jpg', 'jpeg', 'gif'}:
                    # Unique name per upload, so browsers don't show a cached old avatar
                    filename = save_upload(avatar_file, f"avatar_{session['user_id']}")

                    conn.execute('UPDATE users SET avatar = ? WHERE id = ?', (filename, session['user_id']))

            conn.execute('UPDATE users SET nickname = ?, phone = ?, city = ?, district = ? WHERE id = ?',
                         (nickname, phone, city, district, session['user_id']))
            conn.commit()
            session['user_name'] = nickname  # Update session if nickname changed
            flash('會員資料已更新', 'success')
            return redirect(url_for('member.member_profile'))
        except Exception as e:
            flash('更新失敗，請稍後再試', 'error')
            
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    
    # Fetch bank accounts
    bank_accounts = conn.execute('SELECT * FROM user_bank_accounts WHERE user_id = ? ORDER BY created_at DESC', 
                                (session['user_id'],)).fetchall()
    
    return render_template('member_profile.html', user=user, bank_accounts=[dict(b) for b in bank_accounts], active_page='profile')

@bp.route('/member/orders')
@login_required
def member_orders():
    # Get filters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
        
    conn = get_db()
    orders_rows = conn.execute('''
        SELECT o.*, 
               u.name as trainer_name, u.nickname as trainer_nickname, u.avatar as trainer_avatar,
               l.pet_name, l.pet_type, l.pet_image
        FROM orders o
        LEFT JOIN listings l ON o.listing_id = l.id
        JOIN users u ON o.seller_id = u.id
        WHERE o.buyer_id = ?
        ORDER BY o.booking_date DESC, o.booking_time DESC
    ''', (session['user_id'],)).fetchall()

    today = date.today().strftime('%Y-%m-%d')

    # Base query wrapper function for filters
    def apply_date_filter(query, base_params):
        if start_date:
            query += " AND o.booking_date >= ?"
            base_params.append(start_date)
        if end_date:
            query += " AND o.booking_date <= ?"
            base_params.append(end_date)
        return query, tuple(base_params)

    # Exclude 'inquiry' status from all lists
    upcoming_orders_query = '''
        SELECT o.*, l.pet_name, l.pet_type, l.available_slots, u.name as trainer_name, u.nickname as trainer_nickname,
               a.status as appeal_status, a.id as appeal_id
        FROM orders o
        LEFT JOIN listings l ON o.listing_id = l.id
        JOIN users u ON o.seller_id = u.id
        LEFT JOIN order_appeals a ON o.id = a.order_id
        WHERE o.buyer_id = ? 
        AND ((o.status = "confirmed" AND o.booking_date >= ?) OR o.status = "in_progress")
        AND o.status != 'inquiry'
    '''
    upcoming_params = [session['user_id'], today]
    upcoming_orders_query, upcoming_params = apply_date_filter(upcoming_orders_query, upcoming_params)
    upcoming_orders_query += " ORDER BY o.booking_date, o.booking_time"
    upcoming_orders_rows = conn.execute(upcoming_orders_query, upcoming_params).fetchall()

    history_orders_query = '''
        SELECT o.*, l.pet_name, l.pet_type, l.available_slots, u.name as trainer_name, u.nickname as trainer_nickname,
               a.status as appeal_status, a.id as appeal_id
        FROM orders o
        LEFT JOIN listings l ON o.listing_id = l.id
        JOIN users u ON o.seller_id = u.id
        LEFT JOIN order_appeals a ON o.id = a.order_id
        WHERE o.buyer_id = ? 
        AND ((o.status != "confirmed" AND o.status != 'pending' AND o.status != 'in_progress') OR (o.status = "confirmed" AND o.booking_date < ?))
        AND o.status != 'inquiry'
    '''
    history_params = [session['user_id'], today]
    history_orders_query, history_params = apply_date_filter(history_orders_query, history_params)
    history_orders_query += " ORDER BY o.booking_date DESC, o.booking_time DESC"
    history_orders_rows = conn.execute(history_orders_query, history_params).fetchall()
    
    # Pending orders (status = 'pending')
    pending_orders_query = '''
        SELECT o.*, l.pet_name, l.pet_type, l.available_slots, u.name as trainer_name, u.nickname as trainer_nickname
        FROM orders o
        LEFT JOIN listings l ON o.listing_id = l.id
        JOIN users u ON o.seller_id = u.id
        WHERE o.buyer_id = ? AND o.status = "pending"
    '''
    pending_params = [session['user_id']]
    pending_orders_query, pending_params = apply_date_filter(pending_orders_query, pending_params)
    pending_orders_query += " ORDER BY o.booking_date, o.booking_time"
    pending_orders_rows = conn.execute(pending_orders_query, pending_params).fetchall()
    
    # Process orders (add has_reviewed, time_range, pet_type translation, total_price)
    def process_order_list(order_list_rows):
        processed_list = []
        for row in order_list_rows:
            o = dict(row)
            # Check if already reviewed by current user (buyer)
            has_reviewed = conn.execute('SELECT 1 FROM reviews WHERE order_id = ? AND source_user_id = ?', (o['id'], session['user_id'])).fetchone()
            o['has_reviewed'] = True if has_reviewed else False
            
            # Translate pet_type
            if o.get('pet_type') == 'dog':
                o['pet_type'] = '狗'
            elif o.get('pet_type') == 'cat':
                o['pet_type'] = '貓'
            
            # Calculate Time Range
            start_h = int(o['booking_time'].split(':')[0])
            duration = o['duration'] if o['duration'] else 1
            end_h = start_h + duration
            o['time_range'] = f"{start_h:02d}:00 - {end_h:02d}:00"
            
            # Ensure total_price
            if not o['total_price']:
                 o['total_price'] = (o['price_at_booking'] or 0) * duration
            
            # Check latest reschedule request status
            resch_req = conn.execute('SELECT status FROM reschedule_requests WHERE order_id = ? ORDER BY created_at DESC LIMIT 1', (o['id'],)).fetchone()
            o['reschedule_status'] = resch_req['status'] if resch_req else None
            
            # Count successful reschedules
            resch_count = conn.execute("SELECT COUNT(*) FROM reschedule_requests WHERE order_id = ? AND status = 'approved'", (o['id'],)).fetchone()[0]
            o['reschedule_count'] = resch_count
            
            # Latest refund task (pending or processed), if any
            refund_task = conn.execute('SELECT amount, status FROM refund_tasks WHERE order_id = ? ORDER BY created_at DESC LIMIT 1', (o['id'],)).fetchone()
            o['refund_amount'] = refund_task['amount'] if refund_task else 0
            o['refund_status'] = refund_task['status'] if refund_task else None

            # Ensure original_price
            if o.get('original_price') is None:
                o['original_price'] = o['total_price']

            processed_list.append(o)
        return processed_list

    pending_orders = process_order_list(pending_orders_rows)
    upcoming_orders = process_order_list(upcoming_orders_rows)
    history_orders = process_order_list(history_orders_rows)

    # Calculate monthly cancellation count for display
    now = datetime.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    monthly_cancel_count = conn.execute('''
        SELECT COUNT(*) FROM orders 
        WHERE buyer_id = ? AND status = 'cancelled' AND updated_at >= ?
    ''', (session['user_id'], start_of_month)).fetchone()[0]
    
    return render_template('member_orders.html', 
                           active_page='orders',
                           pending_orders=pending_orders,
                           upcoming_orders=upcoming_orders,
                           history_orders=history_orders,
                           monthly_cancel_count=monthly_cancel_count)

@bp.route('/member/order/<int:order_id>/appeal', methods=('GET', 'POST'))
@login_required
def member_order_appeal(order_id):
    conn = get_db()
    order = conn.execute('''
        SELECT o.*, 
               l.pet_name, l.pet_type,
               u.name as trainer_name, u.nickname as trainer_nickname
        FROM orders o
        JOIN listings l ON o.listing_id = l.id
        JOIN users u ON o.seller_id = u.id
        WHERE o.id = ? AND o.buyer_id = ?
    ''', (order_id, session['user_id'])).fetchone()
    
    if not order:
        flash('訂單不存在', 'error')
        return redirect(url_for('member.member_orders'))
        
    # Check if appeal already exists
    existing_appeal = conn.execute('SELECT * FROM order_appeals WHERE order_id = ?', (order_id,)).fetchone()
    if existing_appeal:
        flash('您已對此訂單提出過申訴', 'info')
        return redirect(url_for('member.member_orders'))

    if request.method == 'POST':
        reason = request.form['reason']
        description = request.form['description']
        
        try:
            conn.execute(f'''
                INSERT INTO order_appeals (order_id, user_id, reason, description, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', {NOW_SQL})
            ''', (order_id, session['user_id'], reason, description))
            conn.commit()
            # Order status is left unchanged; admins see the appeal separately.
            flash('申訴已送出，客服將儘快為您處理', 'success')
            return redirect(url_for('member.member_orders'))
        except Exception as e:
            flash(f'提交失敗: {str(e)}', 'error')
            
    return render_template('member_order_appeal.html', order=order, active_page='orders')

@bp.route('/member/appeal/<int:appeal_id>', methods=('GET', 'POST'))
@login_required
def member_appeal_detail(appeal_id):
    conn = get_db()
    
    # Fetch appeal with order and seller info
    appeal = conn.execute('''
        SELECT a.*, 
               o.booking_date, o.booking_time, o.total_price, o.status as order_status,
               l.pet_name, l.pet_type,
               u.name as seller_name, u.nickname as seller_nickname, u.email as seller_email
        FROM order_appeals a
        JOIN orders o ON a.order_id = o.id
        JOIN listings l ON o.listing_id = l.id
        JOIN users u ON o.seller_id = u.id
        WHERE a.id = ? AND a.user_id = ?
    ''', (appeal_id, session['user_id'])).fetchone()
    
    if not appeal:
        flash('申訴案件不存在', 'error')
        return redirect(url_for('member.member_orders'))
    
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        
        if message and appeal['status'] not in ['closed', 'resolved']:
            try:
                conn.execute(f'''
                    INSERT INTO appeal_messages (appeal_id, sender_id, sender_role, message, created_at)
                    VALUES (?, ?, 'user', ?, {NOW_SQL})
                ''', (appeal_id, session['user_id'], message))
                conn.commit()
                flash('留言已送出', 'success')
            except Exception as e:
                flash(f'送出失敗: {str(e)}', 'error')
            return redirect(url_for('member.member_appeal_detail', appeal_id=appeal_id))
        else:
            if not message:
                flash('請輸入留言內容', 'error')
    
    # Fetch all messages for this appeal
    messages = conn.execute('''
        SELECT m.*, u.name as sender_name, u.nickname as sender_nickname
        FROM appeal_messages m
        JOIN users u ON m.sender_id = u.id
        WHERE m.appeal_id = ?
        ORDER BY m.created_at ASC
    ''', (appeal_id,)).fetchall()
    
    return render_template('member_appeal_detail.html', appeal=appeal, messages=messages, active_page='orders')

@bp.route('/member/order/<int:order_id>/payment')
@login_required
def member_payment(order_id):
    conn = get_db()
    order = conn.execute('''
        SELECT o.*, 
               u.name as trainer_name, u.nickname as trainer_nickname,
               l.pet_name
        FROM orders o
        JOIN listings l ON o.listing_id = l.id
        JOIN users u ON o.seller_id = u.id
        WHERE o.id = ? AND o.buyer_id = ?
    ''', (order_id, session['user_id'])).fetchone()
    
    if not order:
        flash('訂單不存在', 'error')
        return redirect(url_for('member.member_orders'))
    
    order = dict(order)
    
    # Check if order is confirmed and unpaid
    if order['status'] != OrderStatus.CONFIRMED:
        flash('此訂單無法付款（狀態不正確）', 'error')
        return redirect(url_for('member.member_orders'))
    
    # Calculate remaining amount checking paid_amount
    paid_amount = order.get('paid_amount', 0) or 0
    remaining_amount = order['total_price'] - paid_amount
    
    if remaining_amount <= 0 and order.get('payment_status') == PaymentStatus.PAID:
        flash('此訂單已付款', 'info')
        return redirect(url_for('member.member_orders'))
        
    order['remaining_amount'] = remaining_amount

    # Fetch saved payment methods
    saved_methods = conn.execute('SELECT * FROM saved_payment_methods WHERE user_id = ? ORDER BY created_at DESC', (session['user_id'],)).fetchall()
    saved_methods = [dict(m) for m in saved_methods]
    
    return render_template('member_payment.html', order=order, active_page='orders', saved_methods=saved_methods)

@bp.route('/member/order/<int:order_id>/payment', methods=['POST'])
@login_required
def process_payment(order_id):
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE id = ? AND buyer_id = ?', 
                        (order_id, session['user_id'])).fetchone()
    
    if not order:
        flash('訂單不存在', 'error')
        return redirect(url_for('member.member_orders'))
        
    order = dict(order)
    
    if order['status'] != OrderStatus.CONFIRMED:
        flash('此訂單無法付款（狀態不正確）', 'error')
        return redirect(url_for('member.member_orders'))
    
    if order.get('payment_status') == PaymentStatus.PAID:
        flash('此訂單已付款', 'info')
        return redirect(url_for('member.member_orders'))
    
    # Check if using saved method
    saved_method_id = request.form.get('saved_method_id')
    save_card = request.form.get('save_card')
    
    payment_success = False
    card_last4 = ''
    
    if saved_method_id:
        # Using saved method
        method = conn.execute('SELECT * FROM saved_payment_methods WHERE id = ? AND user_id = ?', 
                             (saved_method_id, session['user_id'])).fetchone()
        if not method:
            flash('無效的付款方式', 'error')
            return redirect(url_for('member.member_payment', order_id=order_id))
        
        # Simulate processing with token
        payment_success = True
        card_last4 = method['card_number_masked'][-4:]
        
    else:
        # Using new card
        card_number = request.form.get('card_number', '').replace(' ', '')
        expiry_month = request.form.get('expiry_month')
        expiry_year = request.form.get('expiry_year')
        cvv = request.form.get('cvv')
        cardholder_name = request.form.get('cardholder_name')
        
        # Basic validation
        if not all([card_number, expiry_month, expiry_year, cvv, cardholder_name]):
            flash('請填寫完整的付款資訊', 'error')
            return redirect(url_for('member.member_payment', order_id=order_id))
        
        if len(card_number) < 13 or len(card_number) > 16:
            flash('信用卡號碼格式不正確', 'error')
            return redirect(url_for('member.member_payment', order_id=order_id))

        # Simulate Payment
        payment_success = True
        card_last4 = card_number[-4:]
        
        # Save card if requested
        if save_card and payment_success:
            try:
                masked_card = f"**** **** **** {card_last4}"
                expiry = f"{expiry_month}/{expiry_year}"
                token = str(uuid.uuid4())

                conn.execute(f'''
                    INSERT INTO saved_payment_methods (user_id, card_number_masked, card_expiry, cardholder_name, payment_token, created_at)
                    VALUES (?, ?, ?, ?, ?, {NOW_SQL})
                ''', (session['user_id'], masked_card, expiry, cardholder_name, token))
            except Exception:
                current_app.logger.exception('Failed to save card')

    if payment_success:
        time.sleep(1) # Simulate delay

        payment_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Calculate amount paid (difference if supplement)
        current_paid = order.get('paid_amount') or 0
        payment_amount = order['total_price'] - current_paid
        
        # Update order status
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
        
        # Record transaction (User Ledger - Payment Out)
        conn.execute(f'''
            INSERT INTO account_transactions (user_id, amount, transaction_type, status, description, created_at)
            VALUES (?, ?, 'payment', 'completed', ?, {NOW_SQL})
        ''', (session['user_id'], -payment_amount, f"支付訂單 #{order_id}"))
        
        conn.commit()
        # The platform balance is credited later, when an admin confirms the payment.

        flash('付款成功！等待管理員確認。', 'success')
        return redirect(url_for('member.member_orders'))
    
    flash('付款失敗，請重試', 'error')
    return redirect(url_for('member.member_payment', order_id=order_id))

        
@bp.route('/member/apply-trainer', methods=('GET', 'POST'))
@login_required
def member_apply_trainer():
    if request.method == 'POST':
        experience = request.form['experience']
        file = request.files['certificate']
        
        if file:
            filename = save_upload(file, f"user_{session['user_id']}")

            conn = get_db()
            conn.execute(f'INSERT INTO trainer_applications (user_id, experience_years, certificate_filename, created_at) VALUES (?, ?, ?, {NOW_SQL})',
                         (session['user_id'], experience, filename))
            conn.commit()
            
            flash('申請已送出，請等待審核', 'success')
            return redirect(url_for('member.member_apply_trainer'))

    # Fetch latest application status
    conn = get_db()
    existing_app = conn.execute('SELECT * FROM trainer_applications WHERE user_id = ? ORDER BY created_at DESC LIMIT 1', (session['user_id'],)).fetchone()
            
    return render_template('member_apply_trainer.html', active_page='apply_trainer', application=existing_app)
