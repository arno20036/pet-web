"""Trainer pages under /trainer/."""
import json
import os
import re
from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from pet_platform.constants import OrderStatus, PaymentStatus, Role, TrainerLevel
from pet_platform.db import get_db
from pet_platform.decorators import role_required
from pet_platform.services.ledger_service import get_platform_settings, process_order_payment_to_trainer
from pet_platform.utils.time import NOW_SQL
from pet_platform.utils.uploads import save_upload

bp = Blueprint('trainer', __name__)


@bp.route('/trainer/create-listing', methods=('GET', 'POST'))
@role_required(Role.TRAINER)
def trainer_create_listing():
    if request.method == 'POST':
        pet_type = request.form['pet_type']
        breed = request.form['breed']
        pet_name = request.form.get('pet_name', '') # Get pet nickname
        price = request.form['price']
        cancellation_hours = request.form['cancellation_hours']
        
        # Process availability
        selected_days = request.form.getlist('days')
        start_time = request.form['start_time']
        end_time = request.form['end_time']
        available_slots = f"{', '.join(selected_days)} {start_time} - {end_time}"
        
        introduction = request.form['introduction']
        rules = request.form['rules']
        files = request.files.getlist('pet_images')
        
        # If user didn't refresh, they might send 'pet_image' (singular)
        if not files:
             files = request.files.getlist('pet_image')
        
        if files and files[0].filename != '':
            conn = get_db()
            cursor = conn.cursor()
            
            saved_filenames = []
            cover_image = None
            
            for i, file in enumerate(files):
                if file.filename:
                     filename = save_upload(file, f"listing_{session['user_id']}_{pet_type}_{i}")
                     saved_filenames.append(filename)

                     if i == 0:
                         cover_image = filename
            
            if saved_filenames:
                cursor.execute('''
                    INSERT INTO listings (trainer_id, pet_image, pet_type, breed, pet_name, available_slots, price, introduction, rules, cancellation_hours)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (session['user_id'], cover_image, pet_type, breed, pet_name, available_slots, price, introduction, rules, cancellation_hours))
                
                listing_id = cursor.lastrowid
                
                # Insert all images into listing_images
                for filename in saved_filenames:
                    cursor.execute('INSERT INTO listing_images (listing_id, filename) VALUES (?, ?)', (listing_id, filename))
                
                conn.commit()
                flash('刊登成功！', 'success')
            else:
                 flash('請至少上傳一張圖片', 'error')
                 
            return redirect(url_for('trainer.trainer_manage_listings'))
            
    return render_template('trainer_create_listing.html', title='刊登', active_page='create_listing')

@bp.route('/trainer/manage-listings')
@role_required(Role.TRAINER)
def trainer_manage_listings():
    conn = get_db()
    listings = conn.execute('SELECT * FROM listings WHERE trainer_id = ? ORDER BY created_at DESC', (session['user_id'],)).fetchall()
    
    published = [l for l in listings if l['status'] == 'approved']
    unpublished = [l for l in listings if l['status'] == 'closed']
    history_approved = [l for l in listings if l['status'] in ['approved', 'closed']]
    history_pending = [l for l in listings if l['status'] in ['pending', 'modification_pending']]
    history_rejected = [l for l in listings if l['status'] == 'rejected']
    
    return render_template('trainer_manage_listings.html', 
                           title='管理刊登內容', 
                           active_page='manage_listings',
                           published=published,
                           unpublished=unpublished,
                           history_approved=history_approved,
                           history_pending=history_pending,
                           history_rejected=history_rejected)

@bp.route('/trainer/listing/<int:listing_id>/close', methods=['POST'])
@role_required(Role.TRAINER)
def trainer_close_listing(listing_id):
    conn = get_db()
    # Verify ownership
    listing = conn.execute('SELECT * FROM listings WHERE id = ? AND trainer_id = ?', (listing_id, session['user_id'])).fetchone()
    if listing and listing['status'] == 'approved':
        conn.execute("UPDATE listings SET status = 'closed' WHERE id = ?", (listing_id,))
        conn.commit()
        flash('刊登已下架', 'success')
    
    return redirect(url_for('trainer.trainer_manage_listings'))

@bp.route('/trainer/listing/<int:listing_id>/republish', methods=['POST'])
@role_required(Role.TRAINER)
def trainer_republish_listing(listing_id):
    conn = get_db()
    # Verify ownership
    listing = conn.execute('SELECT * FROM listings WHERE id = ? AND trainer_id = ?', (listing_id, session['user_id'])).fetchone()
    if listing and listing['status'] == 'closed':
        conn.execute("UPDATE listings SET status = 'approved' WHERE id = ?", (listing_id,))
        conn.commit()
        flash('刊登已重新上架', 'success')
    
    return redirect(url_for('trainer.trainer_manage_listings'))

@bp.route('/trainer/listing/<int:listing_id>/cancel-modification', methods=['POST'])
@role_required(Role.TRAINER)
def trainer_cancel_modification(listing_id):
    conn = get_db()
    # Verify ownership and status
    listing = conn.execute('SELECT * FROM listings WHERE id = ? AND trainer_id = ?', (listing_id, session['user_id'])).fetchone()
    
    if listing and listing['status'] == 'modification_pending':
        # Revert to 'closed' (unpublished) as requested
        # Note: This keeps the modified content but stops the review process.
        conn.execute("UPDATE listings SET status = 'closed' WHERE id = ?", (listing_id,))
        conn.commit()
        flash('修改申請已取消，刊登已移至「已下架」列表', 'success')
    else:
        flash('無法取消：找不到該申請或狀態不正確', 'error')
    
    return redirect(url_for('trainer.trainer_manage_listings'))

@bp.route('/trainer/listing/<int:listing_id>/delete', methods=['POST'])
@role_required(Role.TRAINER)
def trainer_delete_listing(listing_id):
    conn = get_db()
    # Verify ownership
    listing = conn.execute('SELECT * FROM listings WHERE id = ? AND trainer_id = ?', (listing_id, session['user_id'])).fetchone()
    
    if listing and listing['status'] == 'closed':
        # Delete images first
        conn.execute('DELETE FROM listing_images WHERE listing_id = ?', (listing_id,))
        # Delete listing
        conn.execute('DELETE FROM listings WHERE id = ?', (listing_id,))
        conn.commit()
        flash('刊登已永久刪除', 'success')
    else:
        flash('無法刪除：刊登不存在或非下架狀態', 'error')
    
    return redirect(url_for('trainer.trainer_manage_listings'))

@bp.route('/trainer/listing/<int:listing_id>')
@role_required(Role.TRAINER)
def trainer_listing_detail(listing_id):
    conn = get_db()
    # Verify ownership
    listing = conn.execute('SELECT * FROM listings WHERE id = ? AND trainer_id = ?', (listing_id, session['user_id'])).fetchone()
    
    if listing:
        # Fetch other images
        images = conn.execute('SELECT filename FROM listing_images WHERE listing_id = ? ORDER BY id ASC', (listing_id,)).fetchall()
        additional_images = [img['filename'] for img in images]
    else:
        additional_images = []

    if listing is None:
        flash('找不到該刊登', 'error')
        return redirect(url_for('trainer.trainer_manage_listings'))
        
    return render_template('trainer_listing_detail.html', listing=listing, images=additional_images, active_page='manage_listings')

@bp.route('/trainer/listing/<int:listing_id>/edit', methods=('GET', 'POST'))
@role_required(Role.TRAINER)
def trainer_listing_edit(listing_id):
    conn = get_db()
    listing = conn.execute('SELECT * FROM listings WHERE id = ? AND trainer_id = ?', (listing_id, session['user_id'])).fetchone()
    
    if listing is None:
        flash('找不到該刊登', 'error')
        return redirect(url_for('trainer.trainer_manage_listings'))

    # Any listing (including approved ones) can be edited; saving sends it back to review
    # as modification_pending.
    if request.method == 'POST':
        price = request.form['price']
        cancellation_hours = request.form['cancellation_hours']
        
        # Process availability
        selected_days = request.form.getlist('days')
        start_time = request.form['start_time']
        end_time = request.form['end_time']
        available_slots = f"{', '.join(selected_days)} {start_time} - {end_time}"
        
        introduction = request.form['introduction']
        rules = request.form['rules']
        
        # Handle New Images
        files = request.files.getlist('new_pet_images')
        saved_new_filenames = []
        if files:
            for i, file in enumerate(files):
                 if file.filename:
                     filename = save_upload(file, f"listing_{session['user_id']}_{listing['pet_type']}")
                     saved_new_filenames.append(filename)

                     conn.execute('INSERT INTO listing_images (listing_id, filename) VALUES (?, ?)', (listing_id, filename))
        
        # Handle Deletions
        delete_filenames = request.form.getlist('delete_images')
        if delete_filenames:
            for filename in delete_filenames:
                conn.execute('DELETE FROM listing_images WHERE listing_id = ? AND filename = ?', (listing_id, filename))
                # Best effort: the DB row is what matters, a missing file is fine
                try:
                    os.remove(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                except:
                    pass
        
        # Update Listing Info AND Status
        # If it was approved (or any other status), it goes to modification_pending
        conn.execute('''
            UPDATE listings 
            SET price = ?, cancellation_hours = ?, available_slots = ?, introduction = ?, rules = ?, status = 'modification_pending'
            WHERE id = ?
        ''', (price, cancellation_hours, available_slots, introduction, rules, listing_id))
        
        # Re-evaluate Cover Image
        first_image = conn.execute('SELECT filename FROM listing_images WHERE listing_id = ? ORDER BY id ASC LIMIT 1', (listing_id,)).fetchone()
        
        if first_image:
            new_cover = first_image['filename']
            conn.execute('UPDATE listings SET pet_image = ? WHERE id = ?', (new_cover, listing_id))

        conn.commit()
        
        flash('修改申請已送出，請等待管理員審核。', 'success')
        return redirect(url_for('trainer.trainer_manage_listings'))
    
    # Fetch images for display
    images = conn.execute('SELECT filename FROM listing_images WHERE listing_id = ? ORDER BY id ASC', (listing_id,)).fetchall()
    image_list = [img['filename'] for img in images]

    # Parse available_slots for pre-filling form
    # Format: "週一, 週二 09:00 - 18:00"
    slot_data = {
        'days': [],
        'start_time': '09:00',
        'end_time': '18:00'
    }
    
    if listing['available_slots']:
        match = re.match(r'^(.*) (\d{2}:\d{2}) - (\d{2}:\d{2})$', listing['available_slots'])
        if match:
            days_str = match.group(1)
            slot_data['days'] = [day.strip() for day in days_str.split(',')]
            slot_data['start_time'] = match.group(2)
            slot_data['end_time'] = match.group(3)

    return render_template('trainer_edit_listing.html', listing=listing, images=image_list, slot_data=slot_data, active_page='manage_listings')

@bp.route('/trainer/calendar')
@role_required(Role.TRAINER)
def trainer_calendar():
    conn = get_db()
    # Fetch confirmed, in_progress, and completed orders
    orders_rows = conn.execute('''
        SELECT o.*, 
               u.name as buyer_name, 
               l.pet_name
        FROM orders o
        JOIN users u ON o.buyer_id = u.id
        LEFT JOIN listings l ON o.listing_id = l.id
        WHERE o.seller_id = ? AND o.status IN ('confirmed', 'in_progress', 'completed')
        ORDER BY o.booking_date ASC, o.booking_time ASC
    ''', (session['user_id'],)).fetchall()
    
    orders_list = []
    for row in orders_rows:
        o = dict(row)
        # Translation for JS
        if o['status'] == OrderStatus.CONFIRMED:
            o['status_text'] = '已接受'
        elif o['status'] == OrderStatus.IN_PROGRESS:
            o['status_text'] = '進行中'
        elif o['status'] == OrderStatus.COMPLETED:
            o['status_text'] = '已完成'
        else:
            o['status_text'] = o['status']
            
        orders_list.append(o)

    today = date.today()

    return render_template('trainer_calendar.html', 
                           title='行事曆', 
                           active_page='trainer_calendar',
                           orders_json=json.dumps(orders_list),
                           current_year=today.year,
                           current_month=today.month)

@bp.route('/trainer/orders')
@role_required(Role.TRAINER)
def trainer_orders():
    conn = get_db()
    # Fetch pending orders with details
    pending_orders_rows = conn.execute('''
        SELECT o.*, 
               u.name as buyer_name, u.phone as buyer_phone, u.nickname as buyer_nickname, u.email as buyer_email, u.avatar as buyer_avatar,
               l.pet_name, l.pet_type
        FROM orders o
        JOIN users u ON o.buyer_id = u.id
        LEFT JOIN listings l ON o.listing_id = l.id
        WHERE o.seller_id = ? AND o.status = 'pending'
        ORDER BY o.created_at ASC
    ''', (session['user_id'],)).fetchall()
    
    # Fetch history orders
    history_orders_rows = conn.execute('''
        SELECT o.*, 
               u.name as buyer_name, u.phone as buyer_phone, u.nickname as buyer_nickname, u.email as buyer_email, u.avatar as buyer_avatar,
               l.pet_name, l.pet_type
        FROM orders o
        JOIN users u ON o.buyer_id = u.id
        LEFT JOIN listings l ON o.listing_id = l.id
        WHERE o.seller_id = ? AND o.status != 'pending' AND o.status != 'inquiry'
        ORDER BY o.booking_date DESC, o.booking_time DESC
    ''', (session['user_id'],)).fetchall()
    
    
    # Helper to process order for display
    def process_orders(rows):
        processed = []
        for row in rows:
            o = dict(row)
            
            # Check if already reviewed by current user (trainer)
            has_reviewed = conn.execute('SELECT 1 FROM reviews WHERE order_id = ? AND source_user_id = ?', (o['id'], session['user_id'])).fetchone()
            o['has_reviewed'] = True if has_reviewed else False
            
            # Ensure listing_pet_type is set (from pet_type) if not already
            if 'pet_type' in o:
                 pt = o['pet_type']
                 if pt == 'dog': pt = '狗'
                 elif pt == 'cat': pt = '貓'
                 o['listing_pet_type'] = pt
            
            # Calculate End Time
            start_h = int(o['booking_time'].split(':')[0])
            duration = o['duration'] if o['duration'] else 1
            end_h = start_h + duration
            o['time_range'] = f"{start_h:02d}:00 - {end_h:02d}:00"
            
            # Ensure total_price
            if not o['total_price']:
                 o['total_price'] = (o['price_at_booking'] or 0) * duration
            
            # Check for pending reschedule request
            reschedule = conn.execute('SELECT * FROM reschedule_requests WHERE order_id = ? AND status = "pending"', (o['id'],)).fetchone()
            if reschedule:
                o['reschedule_request'] = dict(reschedule)
                
            processed.append(o)
        return processed

    pending_orders = process_orders(pending_orders_rows)
    all_history = process_orders(history_orders_rows)

    accepted_orders = []
    ongoing_orders = []
    history_orders = []
    
    for o in all_history:
        if o['status'] == OrderStatus.CONFIRMED:
             accepted_orders.append(o)
        elif o['status'] == OrderStatus.IN_PROGRESS:
             ongoing_orders.append(o)
        else:
            # rejected, cancelled, completed
            history_orders.append(o)

    return render_template('trainer_orders.html', 
                           title='訂單管理', 
                           active_page='trainer_orders',
                           pending_orders=pending_orders,
                           accepted_orders=accepted_orders,
                           ongoing_orders=ongoing_orders,
                           history_orders=history_orders)

@bp.route('/trainer/order/<int:order_id>/<action>', methods=['POST'])
@role_required(Role.TRAINER)
def trainer_order_action(order_id, action):
    if action not in ['accept', 'reject', 'start', 'complete']:
        flash('無效的操作', 'error')
        return redirect(url_for('trainer.trainer_orders'))
        
    conn = get_db()
    # Verify ownership
    order = conn.execute('SELECT * FROM orders WHERE id = ? AND seller_id = ?', (order_id, session['user_id'])).fetchone()
    
    if not order:
        flash('找不到該訂單或無權限', 'error')
        return redirect(url_for('trainer.trainer_orders'))
        
    new_status = None
    msg = ''
    flash_type = 'success'
    
    if action == 'accept':
        # Check if force accept is requested (bypass overlap check)
        force_accept = request.args.get('force', '0') == '1'
        
        # Check for time slot overlap with other confirmed orders on the same day
        order_dict = dict(order)
        booking_date = order_dict.get('booking_date')
        booking_time = order_dict.get('booking_time')  # format: "HH:00"
        duration = order_dict.get('duration', 1)
        
        # Parse the order's time range
        try:
            order_start = int(booking_time.split(':')[0])
            order_end = order_start + duration
        except:
            order_start = 0
            order_end = 1
        
        # Find other confirmed/in-progress orders on the same day for this trainer
        overlapping_orders = conn.execute('''
            SELECT o.*, l.pet_type, l.breed
            FROM orders o
            LEFT JOIN listings l ON o.listing_id = l.id
            WHERE o.seller_id = ?
            AND o.booking_date = ?
            AND o.id != ?
            AND o.status IN ('confirmed', 'in_progress')
        ''', (session['user_id'], booking_date, order_id)).fetchall()
        
        conflicts = []
        for other_order in overlapping_orders:
            other = dict(other_order)
            try:
                other_start = int(other['booking_time'].split(':')[0])
                other_duration = other.get('duration', 1)
                other_end = other_start + other_duration
            except:
                continue
            
            # Check for overlap: [order_start, order_end) overlaps with [other_start, other_end)
            if not (order_end <= other_start or order_start >= other_end):
                title = f"{other.get('pet_type', '寵物')} ({other.get('breed', '未分類')})"
                conflicts.append({
                    'id': other['id'],
                    'time': f"{other_start:02d}:00 - {other_end:02d}:00",
                    'title': title
                })
        
        # If there are conflicts and not force accept, show warning
        if conflicts and not force_accept:
            conflict_info = "、".join([f"訂單#{c['id']}（{c['time']}）" for c in conflicts])
            # Store conflict info in session for JavaScript confirmation dialog
            session['pending_accept_order_id'] = order_id
            session['pending_accept_conflicts'] = conflict_info
            flash(f'OVERLAP_WARNING::{order_id}::{conflict_info}', 'warning')
            return redirect(url_for('trainer.trainer_orders'))
        
        new_status = OrderStatus.CONFIRMED
        msg = '訂單已確認接受！'
        if conflicts:
            msg += '（已略過時段重疊警告）'
    elif action == 'reject':
        new_status = OrderStatus.REJECTED
        msg = '訂單已拒絕'
        flash_type = 'info'
    elif action == 'start':
        # Check if payment is confirmed before allowing start
        order_dict = dict(order)
        if order_dict.get('payment_status') == PaymentStatus.PAID and order_dict.get('payment_confirmed') != 1:
            flash('此訂單尚未確認收款，無法開始行程。請等待管理員確認收款。', 'error')
            return redirect(url_for('trainer.trainer_orders'))
        new_status = OrderStatus.IN_PROGRESS
        msg = '行程已開始！'
    elif action == 'complete':
        new_status = OrderStatus.COMPLETED
        msg = '行程已完成！'
    else:
        flash('無效的操作', 'error')
        return redirect(url_for('trainer.trainer_orders'))
    
    try:
        conn.execute(f"UPDATE orders SET status = ?, updated_at = {NOW_SQL} WHERE id = ?", (new_status, order_id))
        conn.commit()
        
        # Process payment to trainer if order is completed
        if action == 'complete':
            if process_order_payment_to_trainer(order_id):
                commission_rate = get_platform_commission_rate()
                msg += f' 款項已發放至您的帳戶（扣除 {commission_rate}% 平台費用）。'
        
        flash(msg, flash_type)
    except Exception as e:
        pass
    return redirect(url_for('trainer.trainer_orders'))

@bp.route('/trainer/level-history')
@role_required(Role.TRAINER)
def trainer_level_history():
    conn = get_db()
    
    level_history = conn.execute('''
        SELECT * FROM trainer_level_history 
        WHERE trainer_id = ? 
        ORDER BY created_at DESC
    ''', (session['user_id'],)).fetchall()
    
    
    return render_template('trainer_level_history.html', 
                          level_history=level_history, 
                          active_page='trainer_account')

# Trainer Account Routes
@bp.route('/trainer/account')
@role_required(Role.TRAINER)
def trainer_account():
    conn = get_db()

    # Get or create account
    account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?',
                          (session['user_id'],)).fetchone()

    if not account:
        conn.execute(f'''
            INSERT INTO trainer_accounts (user_id, balance, total_earned, created_at, updated_at)
            VALUES (?, 0.0, 0.0, {NOW_SQL}, {NOW_SQL})
        ''', (session['user_id'],))
        conn.commit()
        account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?',
                              (session['user_id'],)).fetchone()

    # Get date filter
    filter_date = request.args.get('date')
    
    # Get transaction history
    sql_query = '''
        SELECT t.*, o.booking_date, o.booking_time
        FROM account_transactions t
        LEFT JOIN orders o ON t.order_id = o.id
        WHERE t.user_id = ?
    '''
    params = [session['user_id']]
    
    if filter_date:
        # Filter by created_at date (SQLite stores datetime string, so we match YYYY-MM-DD%)
        sql_query += ' AND date(t.created_at) = ?'
        params.append(filter_date)
        
    sql_query += ' ORDER BY t.created_at DESC'
    
    transactions_rows = conn.execute(sql_query, params).fetchall()
    
    # Running balance must be computed over ALL transactions, then filtered for display

    all_transactions_rows = conn.execute('''
        SELECT t.*, o.booking_date, o.booking_time
        FROM account_transactions t
        LEFT JOIN orders o ON t.order_id = o.id
        WHERE t.user_id = ?
        ORDER BY t.created_at ASC
    ''', (session['user_id'],)).fetchall()
    
    # Calculate balance for each transaction chronologically
    transactions = []
    current_running_balance = 0.0
    
    # Process all transactions to establish balance history
    for row in all_transactions_rows:
        t = dict(row)
        current_running_balance += t['amount']
        t['balance_after'] = current_running_balance
        transactions.append(t)
    
    # Reverse to show newest first, consistent with display
    transactions.reverse()
    
    # Apply date filter if requested
    if filter_date:
        transactions = [t for t in transactions if t['created_at'].startswith(filter_date)]
    
    # Get trainer level settings
    settings = get_platform_settings()
    current_level = account['current_level'] if 'current_level' in account.keys() and account['current_level'] else TrainerLevel.GENERAL
    
    # Determine current rate for display
    current_rate = settings.get('commission_general', 15.0)
    if current_level == TrainerLevel.PROFESSIONAL:
        current_rate = settings.get('commission_professional', 5.0)
    elif current_level == TrainerLevel.ADVANCED:
        current_rate = settings.get('commission_advanced', 10.0)
    
    # Get withdrawal requests for this trainer
    withdrawal_requests = conn.execute('''
        SELECT * FROM withdrawal_requests 
        WHERE user_id = ?
        ORDER BY created_at DESC
    ''', (session['user_id'],)).fetchall()
    withdrawal_requests = [dict(r) for r in withdrawal_requests]
        

    return render_template('trainer_account.html',
                          title='平台帳戶',
                          active_page='trainer_account',
                          account=dict(account),
                          transactions=transactions,
                          current_level=current_level,
                          commission_rate=current_rate,
                          selected_date=filter_date,
                          withdrawal_requests=withdrawal_requests)

@bp.route('/trainer/withdrawal', methods=['GET', 'POST'])
@role_required(Role.TRAINER)
def trainer_withdrawal():
    conn = get_db()
    
    # Get account balance
    account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?', 
                          (session['user_id'],)).fetchone()
    
    if not account:
        flash('請先完成訂單以建立帳戶', 'error')
        return redirect(url_for('trainer.trainer_account'))
    
    if request.method == 'POST':
        try:
            amount = float(request.form.get('amount', 0))
            bank_code = request.form.get('bank_name')
            bank_account = request.form.get('bank_account')
            account_holder = request.form.get('account_holder')
            save_account = request.form.get('save_account') == 'on'
            
            # Validation
            if amount <= 0:
                flash('提領金額必須大於 0', 'error')
            elif amount > account['balance']:
                flash('提領金額不可超過帳戶餘額', 'error')
            elif not bank_code or not bank_account or not account_holder:
                flash('請填寫完整銀行資訊', 'error')
            elif not bank_code.isdigit() or len(bank_code) != 3:
                flash('銀行代碼必須為 3 位數字', 'error')
            else:
                # Save bank account if requested
                if save_account:
                    # Check if already exists
                    existing = conn.execute('''
                        SELECT id FROM trainer_bank_accounts 
                        WHERE user_id = ? AND bank_code = ? AND bank_account = ?
                    ''', (session['user_id'], bank_code, bank_account)).fetchone()
                    
                    if not existing:
                        conn.execute(f'''
                            INSERT INTO trainer_bank_accounts (user_id, bank_code, bank_account, account_holder, created_at)
                            VALUES (?, ?, ?, ?, {NOW_SQL})
                        ''', (session['user_id'], bank_code, bank_account, account_holder))
                
                # Create withdrawal request
                conn.execute(f'''
                    INSERT INTO withdrawal_requests 
                    (user_id, amount, bank_name, bank_account, account_holder, status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', {NOW_SQL})
                ''', (session['user_id'], amount, bank_code, bank_account, account_holder))
                
                conn.commit()
                flash('提領申請已送出，請等待審核', 'success')
                return redirect(url_for('trainer.trainer_account'))
                
        except ValueError:
            flash('請輸入有效的金額', 'error')

    # Get saved bank accounts
    saved_accounts = conn.execute('''
        SELECT * FROM trainer_bank_accounts WHERE user_id = ? ORDER BY created_at DESC
    ''', (session['user_id'],)).fetchall()
    
    
    return render_template('trainer_withdrawal.html',
                          title='申請提領',
                          active_page='account',
                          account=account,
                          balance=account['balance'],
                          saved_accounts=[dict(row) for row in saved_accounts])

@bp.route('/trainer/reviews')
@role_required(Role.TRAINER)
def trainer_reviews_history():
    filter_date = request.args.get('date')
    conn = get_db()
    
    query = '''
        SELECT r.*, u.name as buyer_name, u.nickname as buyer_nickname, u.avatar as buyer_avatar
        FROM reviews r
        JOIN users u ON r.source_user_id = u.id
        WHERE r.target_user_id = ? AND r.review_type = 'buyer_to_trainer'
    '''
    params = [session['user_id']]
    
    if filter_date:
        query += ' AND date(r.created_at) = ?'
        params.append(filter_date)
        
    query += ' ORDER BY r.created_at DESC'
    
    reviews = conn.execute(query, params).fetchall()
    
    # Calculate stats
    stats_row = conn.execute('''
        SELECT AVG(rating) as avg_rating, COUNT(*) as total_reviews 
        FROM reviews 
        WHERE target_user_id = ? AND review_type = 'buyer_to_trainer'
    ''', (session['user_id'],)).fetchone()
    
    avg_rating = stats_row['avg_rating'] if stats_row and stats_row['avg_rating'] else 0
    total_reviews = stats_row['total_reviews']
    
    
    return render_template('trainer_reviews.html', 
                          reviews=[dict(r) for r in reviews],
                          avg_rating=avg_rating,
                          total_reviews=total_reviews,
                          filter_date=filter_date,
                          active_page='trainer_reviews')

@bp.route('/trainer/blacklist')
@role_required(Role.TRAINER, redirect_to='auth.login')
def trainer_blacklist():
    conn = get_db()
    blacklist = conn.execute('''
        SELECT b.*, u.name, u.nickname, u.email, u.avatar 
        FROM blacklists b
        JOIN users u ON b.consumer_id = u.id
        WHERE b.trainer_id = ?
        ORDER BY b.created_at DESC
    ''', (session['user_id'],)).fetchall()
    
    return render_template('trainer_blacklist.html', blacklist=blacklist, active_page='blacklist')
