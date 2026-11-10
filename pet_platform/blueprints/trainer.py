"""Trainer pages under /trainer/."""
import json
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from pet_platform.constants import OrderStatus, Role, TrainerLevel
from pet_platform.db import get_db
from pet_platform.decorators import role_required
from pet_platform.services import bank_account_service, ledger_service, listing_service, order_service, ServiceError
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
        form = request.form
        fields = {
            'price': form['price'],
            'cancellation_hours': form['cancellation_hours'],
            'available_slots': listing_service.format_slots(form.getlist('days'), form['start_time'], form['end_time']),
            'introduction': form['introduction'],
            'rules': form['rules'],
        }
        listing_service.update_listing(listing, session['user_id'], fields,
                                       request.files.getlist('new_pet_images'), form.getlist('delete_images'))
        flash('修改申請已送出，請等待管理員審核。', 'success')
        return redirect(url_for('trainer.trainer_manage_listings'))

    return render_template('trainer_edit_listing.html', listing=listing,
                           images=listing_service.listing_images(listing_id),
                           slot_data=listing_service.slot_form_data(listing['available_slots']),
                           active_page='manage_listings')

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
    return render_template('trainer_orders.html',
                           title='訂單管理',
                           active_page='trainer_orders',
                           **order_service.list_trainer_orders(session['user_id']))

@bp.route('/trainer/order/<int:order_id>/<action>', methods=['POST'])
@role_required(Role.TRAINER)
def trainer_order_action(order_id, action):
    force = request.args.get('force', '0') == '1'  # accept despite schedule overlap
    try:
        msg, category = order_service.trainer_action(order_id, session['user_id'], action, force)
        flash(msg, category)
    except order_service.ScheduleConflict as e:
        # The page's JavaScript turns this flash into an "accept anyway?" dialog.
        flash(f'OVERLAP_WARNING::{order_id}::{e.conflict_info}', 'warning')
    except ServiceError as e:
        flash(e.message, e.category)
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
    user_id = session['user_id']
    account = ledger_service.get_or_create_trainer_account(user_id)
    filter_date = request.args.get('date')
    current_level = account['current_level'] if 'current_level' in account.keys() and account['current_level'] else TrainerLevel.GENERAL

    return render_template('trainer_account.html',
                          title='平台帳戶',
                          active_page='trainer_account',
                          account=dict(account),
                          transactions=ledger_service.trainer_transactions(user_id, filter_date),
                          current_level=current_level,
                          commission_rate=ledger_service.commission_rate_for_level(current_level),
                          selected_date=filter_date,
                          withdrawal_requests=ledger_service.withdrawal_requests_for(user_id))

@bp.route('/trainer/withdrawal', methods=['GET', 'POST'])
@role_required(Role.TRAINER)
def trainer_withdrawal():
    conn = get_db()
    account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?',
                          (session['user_id'],)).fetchone()
    if not account:
        flash('請先完成訂單以建立帳戶', 'error')
        return redirect(url_for('trainer.trainer_account'))

    if request.method == 'POST':
        form = request.form
        try:
            ledger_service.request_withdrawal(
                session['user_id'], account['balance'], form.get('amount', 0), form.get('bank_name'),
                form.get('bank_account'), form.get('account_holder'), form.get('save_account') == 'on')
            flash('提領申請已送出，請等待審核', 'success')
            return redirect(url_for('trainer.trainer_account'))
        except ServiceError as e:
            flash(e.message, e.category)

    return render_template('trainer_withdrawal.html',
                          title='申請提領',
                          active_page='account',
                          account=account,
                          balance=account['balance'],
                          saved_accounts=bank_account_service.list_accounts('trainer', session['user_id']))

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
