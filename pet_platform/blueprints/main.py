"""Public pages: home, listing detail, booking, uploaded files, trainer listings."""
import sqlite3
from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_from_directory, session, url_for

from pet_platform.constants import Role
from pet_platform.db import get_db
from pet_platform.services import listing_service, order_service, ServiceError

bp = Blueprint('main', __name__)


@bp.route('/')
def index():
    query = request.args.get('q', '')
    category = request.args.get('category', '')
    listings = listing_service.search_listings(query, category, session.get('user_id'))
    top_rated = listing_service.top_rated_listings(category)
    return render_template('index.html', listings=listings, query=query, top_rated=top_rated, category=category)

@bp.route('/booking/<int:listing_id>', methods=('GET', 'POST'))
def booking_create(listing_id):
    if 'user_id' not in session:
        flash('請先登入以進行預約', 'warning')
        return redirect(url_for('auth.login', next=request.url))
        
    conn = get_db()
    listing = conn.execute('''
        SELECT l.*, u.name as trainer_name 
        FROM listings l
        JOIN users u ON l.trainer_id = u.id
        WHERE l.id = ?
    ''', (listing_id,)).fetchone()
    
    if not listing:
        flash('找不到該刊登', 'error')
        return redirect(url_for('main.index'))
        
    # Prevent booking own listing
    if listing['trainer_id'] == session['user_id']:
        flash('您無法預約自己的刊登', 'error')
        return redirect(url_for('main.listing_detail', listing_id=listing_id))
        
    min_hour, max_hour, allowed_weekdays = listing_service.booking_window(listing['available_slots'])

    if request.method == 'POST':
        form = request.form
        try:
            duration, total_price = order_service.create_booking(
                listing, session['user_id'], form['booking_date'], form['start_time'], form['end_time'],
                form['location_type'], form.get('location_detail', ''), allowed_weekdays)
            flash(f'預約申請已送出！時數: {duration}小時，總金額: ${total_price}', 'success')
            return redirect(url_for('member.member_orders'))
        except ServiceError as e:
            flash(e.message, e.category)
            return redirect(request.url)
        except sqlite3.Error as e:
            flash(f'預約失敗: {str(e)}', 'error')

    today_date = date.today().strftime('%Y-%m-%d')
    
    return render_template('booking_create.html', listing=listing, today_date=today_date, min_hour=min_hour, max_hour=max_hour, allowed_weekdays=allowed_weekdays)

@bp.route('/listing/<int:listing_id>')
def listing_detail(listing_id):
    conn = get_db()
    
    # Join with users to get trainer info
    listing = conn.execute('''
        SELECT l.*, u.name as trainer_name, u.nickname as trainer_nickname, u.avatar as trainer_avatar
        FROM listings l
        JOIN users u ON l.trainer_id = u.id
        WHERE l.id = ?
    ''', (listing_id,)).fetchone()
    
    if listing is None:
        flash('找不到該刊登', 'error')
        return redirect(url_for('main.index'))

    # Check Blacklist
    if 'user_id' in session:
        user_id = session['user_id']
        is_blocked = conn.execute('SELECT 1 FROM blacklists WHERE trainer_id = ? AND consumer_id = ?', (listing['trainer_id'], user_id)).fetchone()
        if is_blocked:
            flash('您無法查看此訓練師的內容', 'error')
            return redirect(url_for('main.index'))

    # Access Control:
    # 1. Approved: Public
    # 2. Not Approved: Only Owner (Trainer) or Admin
    current_user_id = session.get('user_id')
    is_owner = current_user_id == listing['trainer_id']
    is_admin = session.get('user_role') == Role.ADMIN
    
    if listing['status'] != 'approved' and not is_owner and not is_admin:
        flash('該刊登尚未上架', 'warning')
        return redirect(url_for('main.index'))
        
    # Fetch images
    images = conn.execute('SELECT filename FROM listing_images WHERE listing_id = ? ORDER BY id ASC', (listing_id,)).fetchall()
    image_list = [img['filename'] for img in images]
    
    # Fetch reviews
    reviews_rows = conn.execute('''
        SELECT r.*, u.name as reviewer_name, u.nickname as reviewer_nickname, u.avatar as reviewer_avatar
        FROM reviews r
        JOIN users u ON r.source_user_id = u.id
        WHERE r.listing_id = ? AND r.review_type = 'buyer_to_trainer'
        ORDER BY r.created_at DESC
    ''', (listing_id,)).fetchall()
    
    reviews = [dict(r) for r in reviews_rows]
    
    # Calculate Average Rating
    average_rating = 0
    if reviews:
        total_rating = sum(r['rating'] for r in reviews)
        average_rating = round(total_rating / len(reviews), 1)
    
    
    return render_template('listing_detail.html', listing=listing, images=image_list, reviews=reviews, average_rating=average_rating)

@bp.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)

@bp.route('/trainer/<int:trainer_id>/listings')
def trainer_public_listings(trainer_id):
    conn = get_db()
    
    # Get trainer info
    trainer = conn.execute('SELECT name, nickname, avatar FROM users WHERE id = ?', (trainer_id,)).fetchone()
    
    if not trainer:
        flash('找不到該訓練師', 'error')
        return redirect(url_for('main.index'))
        
    # Get approved listings
    listings = conn.execute('''
        SELECT * FROM listings 
        WHERE trainer_id = ? AND status = 'approved'
        ORDER BY created_at DESC
    ''', (trainer_id,)).fetchall()
    
    
    page_title = f"{trainer['nickname']} 的所有刊登"
    return render_template('index.html', listings=listings, page_title=page_title)
