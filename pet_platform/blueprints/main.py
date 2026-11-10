"""Public pages: home, listing detail, booking, uploaded files, trainer listings."""
import re
from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_from_directory, session, url_for

from pet_platform.constants import Role
from pet_platform.db import get_db
from pet_platform.utils.time import NOW_SQL

bp = Blueprint('main', __name__)


@bp.route('/')
def index():
    conn = get_db()
    query = request.args.get('q', '')
    category = request.args.get('category', '')
    
    if query:
        # Simple search in pet_name, breed, pet_type, or introduction
        search_pattern = f'%{query}%'
        if category and category in ['dog', 'cat']:
            listings = conn.execute('''
                SELECT * FROM listings 
                WHERE status = 'approved' 
                AND pet_type = ?
                AND (pet_name LIKE ? OR breed LIKE ? OR pet_type LIKE ? OR introduction LIKE ?)
                ORDER BY created_at DESC
            ''', (category, search_pattern, search_pattern, search_pattern, search_pattern)).fetchall()
        else:
            listings = conn.execute('''
                SELECT * FROM listings 
                WHERE status = 'approved' 
                AND (pet_name LIKE ? OR breed LIKE ? OR pet_type LIKE ? OR introduction LIKE ?)
                ORDER BY created_at DESC
            ''', (search_pattern, search_pattern, search_pattern, search_pattern)).fetchall()
    elif category and category in ['dog', 'cat']:
        # Filter by category only
        listings = conn.execute('SELECT * FROM listings WHERE status = "approved" AND pet_type = ? ORDER BY created_at DESC', (category,)).fetchall()
    else:
        # Show latest approved listings
        listings = conn.execute('SELECT * FROM listings WHERE status = "approved" ORDER BY created_at DESC').fetchall()
    
    # Filter out blocked listings
    if 'user_id' in session:
        user_id = session['user_id']
        blocked_trainers = [row['trainer_id'] for row in conn.execute('SELECT trainer_id FROM blacklists WHERE consumer_id = ?', (user_id,)).fetchall()]
        listings = [l for l in listings if l['trainer_id'] not in blocked_trainers]
    
    # Top rated listings - ranked by average review score
    if category and category in ['dog', 'cat']:
        top_rated = conn.execute('''
            SELECT l.*, 
                   COALESCE(AVG(r.rating), 0) as avg_rating,
                   COUNT(r.id) as review_count,
                   u.name as trainer_name, u.nickname as trainer_nickname
            FROM listings l
            LEFT JOIN orders o ON l.id = o.listing_id
            LEFT JOIN reviews r ON o.id = r.order_id AND r.review_type = 'buyer_to_trainer'
            JOIN users u ON l.trainer_id = u.id
            WHERE l.status = 'approved' AND l.pet_type = ?
            GROUP BY l.id
            HAVING avg_rating > 0
            ORDER BY avg_rating DESC, review_count DESC
            LIMIT 6
        ''', (category,)).fetchall()
    else:
        top_rated = conn.execute('''
            SELECT l.*, 
                   COALESCE(AVG(r.rating), 0) as avg_rating,
                   COUNT(r.id) as review_count,
                   u.name as trainer_name, u.nickname as trainer_nickname
            FROM listings l
            LEFT JOIN orders o ON l.id = o.listing_id
            LEFT JOIN reviews r ON o.id = r.order_id AND r.review_type = 'buyer_to_trainer'
            JOIN users u ON l.trainer_id = u.id
            WHERE l.status = 'approved'
            GROUP BY l.id
            HAVING avg_rating > 0
            ORDER BY avg_rating DESC, review_count DESC
            LIMIT 6
        ''').fetchall()
        
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
        
    # Parse available_slots to limit selection
    # Format: "Day1, Day2 HH:MM - HH:MM" e.g. "週一, 週三 09:00 - 18:00"
    min_hour = 9
    max_hour = 22
    allowed_weekdays = [] # 0=Sun, 1=Mon...
    
    if listing['available_slots']:
        # Extract Hours
        match = re.search(r'(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})', listing['available_slots'])
        if match:
            min_hour = int(match.group(1))
            max_hour = int(match.group(3))
            
        # Extract Days
        day_map = {'週日': 0, '週一': 1, '週二': 2, '週三': 3, '週四': 4, '週五': 5, '週六': 6}
        for day_name, day_val in day_map.items():
            if day_name in listing['available_slots']:
                allowed_weekdays.append(day_val)

    if request.method == 'POST':
        booking_date = request.form['booking_date']
        
        # Validate weekday in backend; numbering matches JS getDay(): 0=Sun ... 6=Sat
        y, m, d = map(int, booking_date.split('-'))
        py_weekday = date(y, m, d).strftime("%w")

        if int(py_weekday) not in allowed_weekdays:
             flash(f'該日期 ({booking_date}) 非開放預約日', 'error')
             return redirect(request.url)

        start_time = request.form['start_time'] # "HH:MM"
        end_time = request.form['end_time']     # "HH:MM"
        location_type = request.form['location_type']
        location_detail = request.form.get('location_detail', '')
        
        # Calculate Duration and Price
        s_h = int(start_time.split(':')[0])
        e_h = int(end_time.split(':')[0])
        duration = e_h - s_h
        
        if duration <= 0:
            flash('結束時間必須晚於開始時間', 'error')
            return redirect(request.url)
            
        total_price = listing['price'] * duration
        
        try:
            conn.execute(f'''
                INSERT INTO orders (listing_id, seller_id, buyer_id, booking_date, booking_time, duration, location_type, location_detail, price_at_booking, total_price, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', {NOW_SQL}, {NOW_SQL})
            ''', (listing_id, listing['trainer_id'], session['user_id'], booking_date, start_time, duration, location_type, location_detail, listing['price'], total_price))
            conn.commit()
            
            flash(f'預約申請已送出！時數: {duration}小時，總金額: ${total_price}', 'success')
            return redirect(url_for('member.member_orders'))
            
        except Exception as e:
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
