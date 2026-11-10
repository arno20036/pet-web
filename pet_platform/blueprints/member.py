"""Member (consumer) pages under /member/."""
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from pet_platform.constants import OrderStatus, PaymentStatus
from pet_platform.db import get_db
from pet_platform.decorators import login_required
from pet_platform.services import bank_account_service, order_service, payment_service, ServiceError
from pet_platform.utils.time import NOW_SQL
from pet_platform.utils.uploads import file_extension, save_upload

bp = Blueprint('member', __name__)


@bp.route('/member/bank_account', methods=['POST'])
@login_required
def add_bank_account():
    form = request.form
    if not all([form.get('bank_code'), form.get('account_number'), form.get('account_name')]):
        flash('請填寫完整銀行帳戶資訊', 'error')
        return redirect(url_for('member.member_profile'))
    bank_account_service.add_member_account(session['user_id'], form['bank_code'], form['account_number'],
                                            form['account_name'], branch_code=form.get('branch_code'))
    flash('銀行帳戶已新增', 'success')
    return redirect(url_for('member.member_profile'))

@bp.route('/member/bank_account/<int:account_id>/delete', methods=['POST'])
@login_required
def delete_user_bank_account(account_id):
    bank_account_service.delete_account('member', account_id, session['user_id'])
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
        except Exception:
            current_app.logger.exception('Profile update failed')
            flash('更新失敗，請稍後再試', 'error')

    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()

    bank_accounts = bank_account_service.list_accounts('member', session['user_id'])
    return render_template('member_profile.html', user=user, bank_accounts=bank_accounts, active_page='profile')

@bp.route('/member/orders')
@login_required
def member_orders():
    user_id = session['user_id']
    lists = order_service.list_member_orders(user_id, request.args.get('start_date'), request.args.get('end_date'))
    return render_template('member_orders.html',
                           active_page='orders',
                           monthly_cancel_count=order_service.monthly_cancel_count(user_id),
                           **lists)

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
    try:
        payment_service.pay_order(order_id, session['user_id'], request.form)
    except payment_service.PaymentFormError as e:
        flash(e.message, e.category)
        return redirect(url_for('member.member_payment', order_id=order_id))
    except ServiceError as e:
        flash(e.message, e.category)
        return redirect(url_for('member.member_orders'))
    flash('付款成功！等待管理員確認。', 'success')
    return redirect(url_for('member.member_orders'))


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
