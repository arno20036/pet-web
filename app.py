from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = 'uploads'


DB_NAME = 'database.db'

# Timezone utility - UTC+8 (Taipei Time)
TZ_UTC8 = timezone(timedelta(hours=8))

def get_current_time():
    """Get current time in UTC+8 timezone"""
    return datetime.now(TZ_UTC8)

def format_datetime(dt_str=None):
    """Format datetime string to UTC+8"""
    if dt_str is None:
        return get_current_time().strftime('%Y-%m-%d %H:%M:%S')
    return dt_str

# Jinja2 filter for displaying timestamps in UTC+8
@app.template_filter('utc8')
def utc8_filter(dt_str):
    """Convert timestamp to UTC+8 for display"""
    if not dt_str:
        return ''
    try:
        # Parse the datetime string (assuming it's in UTC or local time)
        if isinstance(dt_str, str):
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        else:
            dt = dt_str
        
        # If datetime is naive (no timezone), assume it's UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        # Convert to UTC+8
        dt_utc8 = dt.astimezone(TZ_UTC8)
        return dt_utc8.strftime('%Y-%m-%d %H:%M:%S')
    except:
        # If parsing fails, return original string
        return str(dt_str)

# Helper functions for trainer account system
def get_platform_settings():
    conn = get_db_connection()
    settings = conn.execute('SELECT * FROM platform_settings LIMIT 1').fetchone()
    conn.close()
    if settings:
        return dict(settings)
    return {
        'commission_rate': 15.0, # Legacy fallback
        'commission_general': 15.0,
        'commission_advanced': 10.0,
        'commission_professional': 5.0,
        'level_adv_threshold': 4.0,
        'level_pro_threshold': 4.5
    }

def get_trainer_commission_rate(trainer_id):
    conn = get_db_connection()
    account = conn.execute('SELECT current_level FROM trainer_accounts WHERE user_id = ?', (trainer_id,)).fetchone()
    conn.close()
    
    settings = get_platform_settings()
    
    level = account['current_level'] if account and account['current_level'] else 'general'
    
    if level == 'professional':
        return settings.get('commission_professional', 5.0)
    elif level == 'advanced':
        return settings.get('commission_advanced', 10.0)
    else:
        # Default or general
        return settings.get('commission_general', 15.0)

def update_trainer_level(trainer_id):
    conn = get_db_connection()
    # specific AVG of rating for buyer_to_trainer reviews?
    # Actually reviews table has review_type. We should filter by review_type='buyer_to_trainer' and target_user_id=trainer_id
    # But wait, submit_review logic says target_user_id is the person being reviewed.
    
    avg_row = conn.execute('''
        SELECT AVG(rating) as avg_rating 
        FROM reviews 
        WHERE target_user_id = ? AND review_type = 'buyer_to_trainer'
    ''', (trainer_id,)).fetchone()
    
    avg_rating = avg_row['avg_rating'] if avg_row and avg_row['avg_rating'] else 0
    
    settings = get_platform_settings()
    new_level = 'general'
    
    if avg_rating >= settings.get('level_pro_threshold', 4.5):
        new_level = 'professional'
    elif avg_rating >= settings.get('level_adv_threshold', 4.0):
        new_level = 'advanced'
        
    # Get current level
    current_level_row = conn.execute('SELECT current_level FROM trainer_accounts WHERE user_id = ?', (trainer_id,)).fetchone()
    current_level = current_level_row['current_level'] if current_level_row else 'general'
        
    conn.execute('UPDATE trainer_accounts SET current_level = ? WHERE user_id = ?', (new_level, trainer_id))
    
    # Record history if level changed
    if current_level != new_level:
        conn.execute('''
            INSERT INTO trainer_level_history (trainer_id, old_level, new_level, trigger_source)
            VALUES (?, ?, ?, 'system_update')
        ''', (trainer_id, current_level, new_level))
        
    conn.commit()
    conn.close()
    return new_level

def get_or_create_trainer_account(user_id):
    """Get or create trainer account"""
    conn = get_db_connection()
    account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?', (user_id,)).fetchone()
    
    if not account:
        conn.execute('''
            INSERT INTO trainer_accounts (user_id, balance, total_earned, created_at, updated_at)
            VALUES (?, 0.0, 0.0, datetime('now', 'localtime'), datetime('now', 'localtime'))
        ''', (user_id,))
        conn.commit()
        account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?', (user_id,)).fetchone()
    
    conn.close()
    return dict(account) if account else None

# Platform Account System
def ensure_platform_tables():
    """Ensure platform account tables exist"""
    conn = get_db_connection()
    # Create platform_account table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS platform_account (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            balance REAL DEFAULT 0.0,
            updated_at TEXT
        )
    ''')
    # Create platform_transactions table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS platform_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            related_order_id INTEGER,
            related_withdrawal_id INTEGER,
            description TEXT,
            created_at TEXT
        )
    ''')
    # Ensure at least one platform account record exists
    existing = conn.execute('SELECT * FROM platform_account LIMIT 1').fetchone()
    if not existing:
        conn.execute('''
            INSERT INTO platform_account (balance, updated_at)
            VALUES (0.0, datetime('now', 'localtime'))
        ''')
    conn.commit()
    conn.close()

def ensure_reschedule_tables():
    """Ensure reschedule and refund tables exist"""
    conn = get_db_connection()
    
    # Reschedule Requests Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reschedule_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            new_date TEXT NOT NULL,
            new_time TEXT NOT NULL,
            new_duration INTEGER NOT NULL,
            price_diff INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', -- pending, approved, rejected
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders (id)
        )
    ''')

    # Refund Tasks Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS refund_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, -- Consumer to refund
            order_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending', -- pending, processed
            processed_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (order_id) REFERENCES orders (id),
            FOREIGN KEY (processed_by) REFERENCES users (id)
        )
    ''')
    
    # User Bank Accounts Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_bank_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bank_code TEXT NOT NULL,
            branch_code TEXT,
            account_number TEXT NOT NULL,
            account_name TEXT NOT NULL,
            is_default BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    conn.commit()
    conn.close()

def get_platform_account():
    """Get platform account balance"""
    ensure_platform_tables()
    conn = get_db_connection()
    account = conn.execute('SELECT * FROM platform_account LIMIT 1').fetchone()
    conn.close()
    return dict(account) if account else {'id': 1, 'balance': 0.0}

def update_platform_balance(amount, transaction_type, description, order_id=None, withdrawal_id=None):
    """Update platform account balance and record transaction"""
    ensure_platform_tables()
    conn = get_db_connection()
    
    # Get current balance
    account = conn.execute('SELECT * FROM platform_account LIMIT 1').fetchone()
    new_balance = account['balance'] + amount
    
    # Update balance
    conn.execute('''
        UPDATE platform_account 
        SET balance = ?, updated_at = datetime('now', 'localtime')
        WHERE id = ?
    ''', (new_balance, account['id']))
    
    # Record transaction
    conn.execute('''
        INSERT INTO platform_transactions 
        (type, amount, related_order_id, related_withdrawal_id, description, created_at)
        VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
    ''', (transaction_type, amount, order_id, withdrawal_id, description))
    
    conn.commit()
    conn.close()
    return new_balance



def process_order_payment_to_trainer(order_id):
    """Process payment to trainer when order is completed"""
    conn = get_db_connection()
    
    # Get order details
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    if not order:
        conn.close()
        return False
    
    order = dict(order)
    
    # Check if payment already processed
    existing_transaction = conn.execute(
        'SELECT * FROM account_transactions WHERE order_id = ? AND type = "income"',
        (order_id,)
    ).fetchone()
    
    if existing_transaction:
        conn.close()
        return False  # Already processed
    
    # Calculate amounts
    trainer_id = order['seller_id']
    commission_rate = get_trainer_commission_rate(trainer_id)
    original_amount = order['total_price']
    commission = round(original_amount * (commission_rate / 100), 2)
    trainer_income = round(original_amount - commission, 2)
    
    # Get or create trainer account
    trainer_id = order['seller_id']
    account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?', (trainer_id,)).fetchone()
    
    if not account:
        conn.execute('''
            INSERT INTO trainer_accounts (user_id, balance, total_earned, created_at, updated_at)
            VALUES (?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))
        ''', (trainer_id, trainer_income, trainer_income))
    else:
        new_balance = account['balance'] + trainer_income
        new_total = account['total_earned'] + trainer_income
        conn.execute('''
            UPDATE trainer_accounts
            SET balance = ?, total_earned = ?, updated_at = datetime('now', 'localtime')
            WHERE user_id = ?
        ''', (new_balance, new_total, trainer_id))
    
    # Record transaction
    conn.execute('''
        INSERT INTO account_transactions 
        (user_id, order_id, amount, commission, original_amount, type, description, created_at)
        VALUES (?, ?, ?, ?, ?, 'income', ?, datetime('now', 'localtime'))
    ''', (trainer_id, order_id, trainer_income, commission, original_amount, 
          f'訂單 #{order_id} 完成收入'))
    
    conn.commit()
    conn.close()
    
    # Deduct trainer income from platform account (Recognize liability)
    update_platform_balance(
        amount=-trainer_income,
        transaction_type='payout_allocation',
        description=f'訂單 #{order_id} 收益分配給訓練師',
        order_id=order_id
    )
    
    return True

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/api/payment_methods/<int:method_id>', methods=['DELETE'])
def delete_payment_method(method_id):
    if 'user_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    conn = get_db_connection()
    conn.execute('DELETE FROM saved_payment_methods WHERE id = ? AND user_id = ?', (method_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return {'success': True}

@app.route('/api/bank_accounts/<int:account_id>', methods=['DELETE'])
def delete_bank_account(account_id):
    if 'user_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    conn = get_db_connection()
    conn.execute('DELETE FROM trainer_bank_accounts WHERE id = ? AND user_id = ?', (account_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return {'success': True}

@app.context_processor
def inject_sidebar_counts():
    counts = {}
    if 'user_id' in session:
        user_id = session['user_id']
        role = session.get('user_role')
        
        try:
            conn = get_db_connection()
            
            if role == 'trainer':
                # Count Pending Orders (Wait for Accept)
                pending_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE seller_id = ? AND status = 'pending'", (user_id,)).fetchone()[0]
                
                # Count Confirmed Orders (Accepted, Wait for Start)
                confirmed_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE seller_id = ? AND status = 'confirmed'", (user_id,)).fetchone()[0]
                
                counts['trainer_pending_orders'] = pending_orders
                counts['trainer_confirmed_orders'] = confirmed_orders
                
                # Calendar Badge: Accepted (confirmed) and Ongoing (in_progress)
                ongoing_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE seller_id = ? AND status = 'in_progress'", (user_id,)).fetchone()[0]
                counts['trainer_calendar_count'] = confirmed_orders + ongoing_orders
                
                # Order Management Badge: Pending only (as requested previously, or keep combined?)
                # User asked to remove red dot from calendar for pending orders.
                # User didn't explicitly say change Order Management badge, but implied pending orders notification belongs there.
                counts['trainer_badge_count'] = pending_orders + confirmed_orders
                
            if role == 'admin':
                # Count Pending Listings
                pending_listings = conn.execute("SELECT COUNT(*) FROM listings WHERE status = 'pending'").fetchone()[0]
                counts['admin_pending_listings'] = pending_listings
                

                
                # Count Pending Trainer Applications
                pending_trainers = conn.execute("SELECT COUNT(*) FROM trainer_applications WHERE status = 'pending'").fetchone()[0]
                counts['admin_pending_trainers'] = pending_trainers

                # Count Pending Withdrawal Requests
                pending_withdrawals = conn.execute("SELECT COUNT(*) FROM withdrawal_requests WHERE status = 'pending'").fetchone()[0]
                counts['admin_pending_withdrawals'] = pending_withdrawals
                
                # Count Pending Payment Confirmations (Paid by buyer but not confirmed by admin)
                # Note: This logic catches "First Time Payments".
                # For "Paid Supplements", they are technically payment_status='paid' and confirmed=0 too.
                # But we might want them in "Refunds" badge if we are strict.
                # However, for simplicity, let them duplicate or split.
                # Actually, "Order Tracking" shows Unconfirmed. "Refunds" shows Unconfirmed Supplements.
                pending_payments = conn.execute("SELECT COUNT(*) FROM orders WHERE payment_status = 'paid' AND (payment_confirmed IS NULL OR payment_confirmed = 0)").fetchone()[0]
                counts['admin_pending_payments'] = pending_payments
                
                # Count Pending Listings Modifications
                pending_modifications = conn.execute("SELECT COUNT(*) FROM listings WHERE status = 'modification_pending'").fetchone()[0]
                counts['admin_pending_modifications'] = pending_modifications

                # Count Pending Refunds & Supplements
                # 1. Refund Tasks
                pending_refund_tasks = conn.execute("SELECT COUNT(*) FROM refund_tasks WHERE status = 'pending'").fetchone()[0]
                
                # 2. Underpaid Orders (Supplements Needed - maybe not urgent for admin? Admin just sees them)
                # Actually Admin usually just waits. Red dot might be distracting if many underpaid?
                # But User asked for "Pending Supplement" page logic.
                pending_supplements_underpaid = conn.execute("SELECT COUNT(*) FROM orders WHERE paid_amount IS NOT NULL AND paid_amount > 0 AND paid_amount < total_price AND status NOT IN ('cancelled', 'rejected')").fetchone()[0]
                
                # 3. Paid Supplements (Need Confirmation)
                # Query matches admin_refunds logic
                paid_supplements_unconfirmed = conn.execute("""
                    SELECT COUNT(*) 
                    FROM orders o
                    WHERE o.payment_confirmed = 0 
                    AND o.payment_status = 'paid' 
                    AND (
                        SELECT COUNT(*) 
                        FROM account_transactions 
                        WHERE user_id = o.buyer_id 
                        AND description = '支付訂單 #' || o.id 
                        AND transaction_type = 'payment'
                    ) > 1
                    AND o.status NOT IN ('cancelled', 'rejected')
                """).fetchone()[0]
                
                counts['admin_tab_refund_count'] = pending_refund_tasks
                counts['admin_tab_supplement_count'] = pending_supplements_underpaid + paid_supplements_unconfirmed
                counts['admin_pending_refunds'] = pending_refund_tasks + pending_supplements_underpaid + paid_supplements_unconfirmed
                
            if role == 'member':
                # Count all active orders for member (pending + confirmed + in_progress)
                # This matches the total badges shown in the order tabs
                pending_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE buyer_id = ? AND status = 'pending'", (user_id,)).fetchone()[0]
                confirmed_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE buyer_id = ? AND status = 'confirmed'", (user_id,)).fetchone()[0]
                in_progress_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE buyer_id = ? AND status = 'in_progress'", (user_id,)).fetchone()[0]
                
                counts['member_pending_orders'] = pending_orders
                counts['member_upcoming_orders'] = confirmed_orders + in_progress_orders
                counts['member_badge_count'] = pending_orders + confirmed_orders + in_progress_orders

            conn.close()
        except Exception as e:
            print(f"Error injecting sidebar counts: {e}")
            
    return dict(sidebar_counts=counts)

@app.route('/')
def index():
    conn = get_db_connection()
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
        
    conn.close()
    return render_template('index.html', listings=listings, query=query, top_rated=top_rated, category=category)

@app.route('/booking/<int:listing_id>', methods=('GET', 'POST'))
def booking_create(listing_id):
    if 'user_id' not in session:
        flash('請先登入以進行預約', 'warning')
        return redirect(url_for('login', next=request.url))
        
    conn = get_db_connection()
    listing = conn.execute('''
        SELECT l.*, u.name as trainer_name 
        FROM listings l
        JOIN users u ON l.trainer_id = u.id
        WHERE l.id = ?
    ''', (listing_id,)).fetchone()
    
    if not listing:
        conn.close()
        flash('找不到該刊登', 'error')
        return redirect(url_for('index'))
        
    # Prevent booking own listing
    if listing['trainer_id'] == session['user_id']:
        conn.close()
        flash('您無法預約自己的刊登', 'error')
        return redirect(url_for('listing_detail', listing_id=listing_id))
        
    # Parse available_slots to limit selection
    # Format: "Day1, Day2 HH:MM - HH:MM" e.g. "週一, 週三 09:00 - 18:00"
    import re
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
        
        # Validate Weekday in Backend
        import datetime
        y, m, d = map(int, booking_date.split('-'))
        weekday = datetime.date(y, m, d).isoweekday() % 7 # isoweekday: 1=Mon..7=Sun -> %7: 1..0 -> Map to 0=Sun, 1=Mon...? 
        # Python datetime.weekday(): 0=Mon, 6=Sun. 
        # JS getDay(): 0=Sun, 1=Mon.
        # Let's align with JS: 0=Sun, 1=Mon, ..., 6=Sat
        py_weekday = datetime.date(y, m, d).strftime("%w") # 0=Sun, 6=Sat
        
        if int(py_weekday) not in allowed_weekdays:
             flash(f'該日期 ({booking_date}) 非開放預約日', 'error')
             return redirect(request.url)
        
        start_time = request.form['start_time'] # "HH:MM"
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
            conn.execute('''
                INSERT INTO orders (listing_id, seller_id, buyer_id, booking_date, booking_time, duration, location_type, location_detail, price_at_booking, total_price, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now', 'localtime'), datetime('now', 'localtime'))
            ''', (listing_id, listing['trainer_id'], session['user_id'], booking_date, start_time, duration, location_type, location_detail, listing['price'], total_price))
            conn.commit()
            
            flash(f'預約申請已送出！時數: {duration}小時，總金額: ${total_price}', 'success')
            return redirect(url_for('member_orders'))
            
        except Exception as e:
            flash(f'預約失敗: {str(e)}', 'error')
            
    conn.close()
    
    import datetime
    today_date = datetime.date.today().strftime('%Y-%m-%d')
    
    return render_template('booking_create.html', listing=listing, today_date=today_date, min_hour=min_hour, max_hour=max_hour, allowed_weekdays=allowed_weekdays)

@app.route('/listing/<int:listing_id>')
def listing_detail(listing_id):
    conn = get_db_connection()
    
    # Join with users to get trainer info
    listing = conn.execute('''
        SELECT l.*, u.name as trainer_name, u.nickname as trainer_nickname, u.avatar as trainer_avatar
        FROM listings l
        JOIN users u ON l.trainer_id = u.id
        WHERE l.id = ?
    ''', (listing_id,)).fetchone()
    
    if listing is None:
        conn.close()
        flash('找不到該刊登', 'error')
        return redirect(url_for('index'))

    # Check Blacklist
    if 'user_id' in session:
        user_id = session['user_id']
        is_blocked = conn.execute('SELECT 1 FROM blacklists WHERE trainer_id = ? AND consumer_id = ?', (listing['trainer_id'], user_id)).fetchone()
        if is_blocked:
            conn.close()
            flash('您無法查看此訓練師的內容', 'error')
            return redirect(url_for('index'))
    
    if listing is None:
        conn.close()
        flash('找不到該刊登', 'error')
        return redirect(url_for('index'))
        
    # Access Control:
    # 1. Approved: Public
    # 2. Not Approved: Only Owner (Trainer) or Admin
    current_user_id = session.get('user_id')
    is_owner = current_user_id == listing['trainer_id']
    is_admin = session.get('user_role') == 'admin'
    
    if listing['status'] != 'approved' and not is_owner and not is_admin:
        conn.close()
        flash('該刊登尚未上架', 'warning')
        return redirect(url_for('index'))
        
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
    
    conn.close()
    
    return render_template('listing_detail.html', listing=listing, images=image_list, reviews=reviews, average_rating=average_rating)

@app.route('/register', methods=('GET', 'POST'))
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        name = request.form['name']
        nickname = request.form['nickname']
        gender = request.form['gender']
        dob = request.form['dob']
        city = request.form['city']
        district = request.form['district']
        phone = request.form['phone']

        if password != confirm_password:
            flash('兩次輸入的密碼不一致', 'error')
            return redirect(url_for('register'))

        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

        if user:
            flash('該電子信箱已被註冊', 'error')
            conn.close()
            return redirect(url_for('register'))

        password_hash = generate_password_hash(password)
        
        try:
            conn.execute('INSERT INTO users (email, password_hash, name, nickname, gender, dob, city, district, phone, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime("now", "localtime"))',
                         (email, password_hash, name, nickname, gender, dob, city, district, phone))
            conn.commit()
            flash('註冊成功，請登入', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash(f'註冊失敗: {str(e)}', 'error')
        finally:
            conn.close()

    return render_template('register.html')

@app.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()

        if user is None:
            flash('帳號不存在', 'error')
        elif not check_password_hash(user['password_hash'], password):
            flash('密碼錯誤', 'error')
        else:
            session.clear()
            session['user_id'] = user['id']
            session['user_name'] = user['nickname']
            session['user_role'] = user['role']
            flash('登入成功', 'success')
            return redirect(url_for('index'))

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('您已登出', 'success')
    return redirect(url_for('login'))

@app.route('/member/bank_account', methods=['POST'])
def add_bank_account():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    bank_code = request.form.get('bank_code')
    branch_code = request.form.get('branch_code')
    account_number = request.form.get('account_number')
    account_name = request.form.get('account_name')
    
    if not all([bank_code, account_number, account_name]):
        flash('請填寫完整銀行帳戶資訊', 'error')
        return redirect(url_for('member_profile'))
        
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO user_bank_accounts (user_id, bank_code, branch_code, account_number, account_name)
        VALUES (?, ?, ?, ?, ?)
    ''', (session['user_id'], bank_code, branch_code, account_number, account_name))
    conn.commit()
    conn.close()
    
    flash('銀行帳戶已新增', 'success')
    return redirect(url_for('member_profile'))

@app.route('/member/bank_account/<int:account_id>/delete', methods=['POST'])
def delete_user_bank_account(account_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    conn.execute('DELETE FROM user_bank_accounts WHERE id = ? AND user_id = ?', (account_id, session['user_id']))
    conn.commit()
    conn.close()
    
    flash('銀行帳戶已刪除', 'success')
    return redirect(url_for('member_profile'))

@app.route('/member/profile', methods=('GET', 'POST'))
def member_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    if request.method == 'POST':
        nickname = request.form['nickname']
        phone = request.form['phone']
        city = request.form['city']
        district = request.form['district']
        
        avatar_file = request.files.get('avatar')
        delete_avatar = request.form.get('delete_avatar')
        
        conn = get_db_connection()
        try:
            # Handle Avatar Deletion
            if delete_avatar == 'true':
                conn.execute('UPDATE users SET avatar = NULL WHERE id = ?', (session['user_id'],))
            
            # Handle Avatar Upload
            if avatar_file and avatar_file.filename:
                # Basic validation (add more if needed)
                if '.' in avatar_file.filename and \
                   avatar_file.filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}:
                    
                    filename = f"avatar_{session['user_id']}_{int(os.path.getmtime(os.getcwd()))}.{avatar_file.filename.rsplit('.', 1)[1].lower()}"
                    # Use unique name to avoid caching issues
                    import time
                    filename = f"avatar_{session['user_id']}_{int(time.time())}.{avatar_file.filename.rsplit('.', 1)[1].lower()}"
                    
                    avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    avatar_file.save(avatar_path)
                    
                    conn.execute('UPDATE users SET avatar = ? WHERE id = ?', (filename, session['user_id']))

            conn.execute('UPDATE users SET nickname = ?, phone = ?, city = ?, district = ? WHERE id = ?',
                         (nickname, phone, city, district, session['user_id']))
            conn.commit()
            session['user_name'] = nickname  # Update session if nickname changed
            flash('會員資料已更新', 'success')
            return redirect(url_for('member_profile'))
        except Exception as e:
            flash('更新失敗，請稍後再試', 'error')
            
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    
    # Fetch bank accounts
    bank_accounts = conn.execute('SELECT * FROM user_bank_accounts WHERE user_id = ? ORDER BY created_at DESC', 
                                (session['user_id'],)).fetchall()
    
    conn.close()
    return render_template('member_profile.html', user=user, bank_accounts=[dict(b) for b in bank_accounts], active_page='profile')

@app.route('/member/orders')
@app.route('/member/orders')
def member_orders():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Get filters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
        
    conn = get_db_connection()
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
    conn.close()
    
    import datetime
    today = datetime.date.today().strftime('%Y-%m-%d')
    
    today = datetime.date.today().strftime('%Y-%m-%d')
    
    conn = get_db_connection() # Re-open connection for the loop
    
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
            
            # Fetch Refund Amount if refunded (or pending refund task)
            # We prioritize 'processed' tasks, but if pending, we might want to know too? 
            # Requirement is "If refunded... add it".
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
    
    conn.close()
    
    # Calculate monthly cancellation count for display
    conn = get_db_connection()
    import datetime
    now = datetime.datetime.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    monthly_cancel_count = conn.execute('''
        SELECT COUNT(*) FROM orders 
        WHERE buyer_id = ? AND status = 'cancelled' AND updated_at >= ?
    ''', (session['user_id'], start_of_month)).fetchone()[0]
    conn.close()
    
    return render_template('member_orders.html', 
                           active_page='orders',
                           pending_orders=pending_orders,
                           upcoming_orders=upcoming_orders,
                           history_orders=history_orders,
                           monthly_cancel_count=monthly_cancel_count)

# ... (omitted code) ...

    pending_orders = process_orders(pending_orders_rows)
    all_history = process_orders(history_orders_rows)
    
    import datetime
    today = datetime.date.today().strftime('%Y-%m-%d')
    
    accepted_orders = []
    ongoing_orders = []
    history_orders = []
    
    for o in all_history:
        if o['status'] == 'confirmed':
             accepted_orders.append(o)
        elif o['status'] == 'in_progress':
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

@app.route('/member/order/<int:order_id>/appeal', methods=('GET', 'POST'))
def member_order_appeal(order_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
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
        conn.close()
        flash('訂單不存在', 'error')
        return redirect(url_for('member_orders'))
        
    # Check if appeal already exists
    existing_appeal = conn.execute('SELECT * FROM order_appeals WHERE order_id = ?', (order_id,)).fetchone()
    if existing_appeal:
        conn.close()
        # If exists, maybe show detail? For now, redirect with message
        flash('您已對此訂單提出過申訴', 'info')
        # Ideally redirect to appeal detail or show in order detail.
        # Let's just return to orders for now.
        return redirect(url_for('member_orders'))

    if request.method == 'POST':
        reason = request.form['reason']
        description = request.form['description']
        
        try:
            conn.execute('''
                INSERT INTO order_appeals (order_id, user_id, reason, description, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', datetime('now', 'localtime'))
            ''', (order_id, session['user_id'], reason, description))
            conn.commit()
            
            # Optionally update order status to 'dispute' or similar?
            # keeping order status as is for now, but admin can see the flag.
            
            flash('申訴已送出，客服將儘快為您處理', 'success')
            return redirect(url_for('member_orders'))
        except Exception as e:
            flash(f'提交失敗: {str(e)}', 'error')
            
    conn.close()
    return render_template('member_order_appeal.html', order=order, active_page='orders')

@app.route('/member/appeal/<int:appeal_id>', methods=('GET', 'POST'))
def member_appeal_detail(appeal_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
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
        conn.close()
        flash('申訴案件不存在', 'error')
        return redirect(url_for('member_orders'))
    
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        
        if message and appeal['status'] not in ['closed', 'resolved']:
            try:
                conn.execute('''
                    INSERT INTO appeal_messages (appeal_id, sender_id, sender_role, message, created_at)
                    VALUES (?, ?, 'user', ?, datetime('now', 'localtime'))
                ''', (appeal_id, session['user_id'], message))
                conn.commit()
                flash('留言已送出', 'success')
            except Exception as e:
                flash(f'送出失敗: {str(e)}', 'error')
            conn.close()
            return redirect(url_for('member_appeal_detail', appeal_id=appeal_id))
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
    
    conn.close()
    return render_template('member_appeal_detail.html', appeal=appeal, messages=messages, active_page='orders')

@app.route('/member/order/<int:order_id>/payment')
def member_payment(order_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    order = conn.execute('''
        SELECT o.*, 
               u.name as trainer_name, u.nickname as trainer_nickname,
               l.pet_name
        FROM orders o
        JOIN listings l ON o.listing_id = l.id
        JOIN users u ON o.seller_id = u.id
        WHERE o.id = ? AND o.buyer_id = ?
    ''', (order_id, session['user_id'])).fetchone()
    conn.close()
    
    if not order:
        flash('訂單不存在', 'error')
        return redirect(url_for('member_orders'))
    
    order = dict(order)
    
    # Check if order is confirmed and unpaid
    if order['status'] != 'confirmed':
        flash('此訂單無法付款（狀態不正確）', 'error')
        return redirect(url_for('member_orders'))
    
    # Calculate remaining amount checking paid_amount
    paid_amount = order.get('paid_amount', 0) or 0
    remaining_amount = order['total_price'] - paid_amount
    
    if remaining_amount <= 0 and order.get('payment_status') == 'paid':
        flash('此訂單已付款', 'info')
        return redirect(url_for('member_orders'))
        
    order['remaining_amount'] = remaining_amount
    
    # Fetch saved payment methods
    conn = get_db_connection()
    saved_methods = conn.execute('SELECT * FROM saved_payment_methods WHERE user_id = ? ORDER BY created_at DESC', (session['user_id'],)).fetchall()
    conn.close()
    saved_methods = [dict(m) for m in saved_methods]
    
    return render_template('member_payment.html', order=order, active_page='orders', saved_methods=saved_methods)

@app.route('/member/order/<int:order_id>/payment', methods=['POST'])
def process_payment(order_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    order = conn.execute('SELECT * FROM orders WHERE id = ? AND buyer_id = ?', 
                        (order_id, session['user_id'])).fetchone()
    
    if not order:
        conn.close()
        flash('訂單不存在', 'error')
        return redirect(url_for('member_orders'))
        
    order = dict(order)
    
    if order['status'] != 'confirmed':
        conn.close()
        flash('此訂單無法付款（狀態不正確）', 'error')
        return redirect(url_for('member_orders'))
    
    if order.get('payment_status') == 'paid':
        conn.close()
        flash('此訂單已付款', 'info')
        return redirect(url_for('member_orders'))
    
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
            conn.close()
            flash('無效的付款方式', 'error')
            return redirect(url_for('member_payment', order_id=order_id))
        
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
            conn.close()
            flash('請填寫完整的付款資訊', 'error')
            return redirect(url_for('member_payment', order_id=order_id))
        
        if len(card_number) < 13 or len(card_number) > 16:
            conn.close()
            flash('信用卡號碼格式不正確', 'error')
            return redirect(url_for('member_payment', order_id=order_id))

        # Simulate Payment
        payment_success = True
        card_last4 = card_number[-4:]
        
        # Save card if requested
        if save_card and payment_success:
            try:
                masked_card = f"**** **** **** {card_last4}"
                expiry = f"{expiry_month}/{expiry_year}"
                import uuid
                token = str(uuid.uuid4())
                
                conn.execute('''
                    INSERT INTO saved_payment_methods (user_id, card_number_masked, card_expiry, cardholder_name, payment_token, created_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
                ''', (session['user_id'], masked_card, expiry, cardholder_name, token))
            except Exception as e:
                print(f"Failed to save card: {e}")

    if payment_success:
        import time
        import datetime
        time.sleep(1) # Simulate delay
        
        # Get listing's cancellation hours logic (simplified reuse or re-fetch if needed)
        # We can re-use existing logic logic or simplify since we are in `confirmed` status
        
        payment_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Update order status
        # Calculate amount paid (difference if supplement)
        current_paid = order.get('paid_amount') or 0
        payment_amount = order['total_price'] - current_paid
        
        # Update order status
        conn.execute('''
            UPDATE orders 
            SET payment_status = 'paid',
                payment_method = 'credit_card',
                payment_time = ?,
                card_last4 = ?,
                payment_confirmed = 0,
                paid_amount = total_price,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
        ''', (payment_time, card_last4, order_id))
        
        # Record transaction (User Ledger - Payment Out)
        conn.execute('''
            INSERT INTO account_transactions (user_id, amount, transaction_type, status, description, created_at)
            VALUES (?, ?, 'payment', 'completed', ?, datetime('now', 'localtime'))
        ''', (session['user_id'], -payment_amount, f"支付訂單 #{order_id}"))
        
        conn.commit()
        conn.close()
        
        # Update Platform Balance REMOVED - Wait for Admin Confirmation
        # Use helper function (separate transaction)
        # desc = f"訂單 #{order_id} 款項"
        # if current_paid > 0:
        #     desc += " (補款)"
        # print(f"DEBUG: Processing Payment - Amount: {payment_amount}, Desc: {desc}, Order: {order_id}")
        # update_platform_balance(payment_amount, 'payment', desc, order_id=order_id)
        
        flash('付款成功！等待管理員確認。', 'success')
        return redirect(url_for('member_orders'))
    
    conn.close()
    flash('付款失敗，請重試', 'error')
    return redirect(url_for('member_payment', order_id=order_id))
        
@app.route('/member/apply-trainer', methods=('GET', 'POST'))
def member_apply_trainer():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        experience = request.form['experience']
        file = request.files['certificate']
        
        if file:
            filename = f"user_{session['user_id']}_{file.filename}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            
            conn = get_db_connection()
            conn.execute('INSERT INTO trainer_applications (user_id, experience_years, certificate_filename, created_at) VALUES (?, ?, ?, datetime("now", "localtime"))',
                         (session['user_id'], experience, filename))
            conn.commit()
            conn.close()
            
            flash('申請已送出，請等待審核', 'success')
            return redirect(url_for('member_apply_trainer'))
            
    # Fetch latest application status
    conn = get_db_connection()
    existing_app = conn.execute('SELECT * FROM trainer_applications WHERE user_id = ? ORDER BY created_at DESC LIMIT 1', (session['user_id'],)).fetchone()
    conn.close()
            
    return render_template('member_apply_trainer.html', active_page='apply_trainer', application=existing_app)

@app.route('/admin/listing-review')
def admin_listing_review():
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    listings = conn.execute('''
        SELECT l.*, u.name as trainer_name, u.email as trainer_email 
        FROM listings l
        JOIN users u ON l.trainer_id = u.id
        WHERE l.status = 'pending'
    ''').fetchall()
    conn.close()
    
    return render_template('admin_listing_review_list.html', listings=listings, active_page='listing_review', title='刊登申請審核')

@app.route('/admin/listing-modification-review')
def admin_listing_modification_review():
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    # Currently we don't have a separate modification status, so we pass empty list or pending?
    # For now, let's pass empty list to show the page structure.
    listings = conn.execute('''
        SELECT l.*, u.name as trainer_name, u.email as trainer_email 
        FROM listings l
        JOIN users u ON l.trainer_id = u.id
        WHERE l.status = 'modification_pending'
    ''').fetchall()
    conn.close()
    
    return render_template('admin_listing_review_list.html', listings=listings, active_page='listing_modification_review', title='刊登修改審核', is_modification=True)

@app.route('/admin/listing-review/<int:listing_id>')
def admin_listing_review_detail(listing_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
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
        
    conn.close()
    
    if listing is None:
        flash('找不到該刊登', 'error')
        return redirect(url_for('admin_listing_review'))
        
    return render_template('admin_listing_review_detail.html', listing=listing, images=additional_images, active_page='listing_review')

@app.route('/admin/listing-review/<int:listing_id>/approve', methods=['POST'])
def admin_listing_approve(listing_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    conn.execute("UPDATE listings SET status = 'approved' WHERE id = ?", (listing_id,))
    conn.commit()
    conn.close()
    
    flash('刊登已核准', 'success')
    return redirect(url_for('admin_listing_review'))

@app.route('/admin/listing-review/<int:listing_id>/reject', methods=['POST'])
def admin_listing_reject(listing_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    conn.execute("UPDATE listings SET status = 'rejected' WHERE id = ?", (listing_id,))
    conn.commit()
    conn.close()
    
    flash('刊登已駁回', 'info')
    return redirect(url_for('admin_listing_review'))

@app.route('/admin/order-tracking')
def admin_order_tracking():
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
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
            'pending': '待接受',
            'confirmed': '已確認',
            'in_progress': '進行中',
            'completed': '已完成',
            'rejected': '已拒絕',
            'cancelled': '已取消'
        }
        order['status_text'] = status_map.get(order['status'], order['status'])
        
        # Translate payment_status
        payment_map = {
            'unpaid': '未付款',
            'paid': '已付款',
            'refunded': '已退款'
        }
        order['payment_status_text'] = payment_map.get(order.get('payment_status', 'unpaid'), '未付款')
        
        orders_list.append(order)
    
    conn.close()
    
    return render_template('admin_order_tracking.html', 
                          title='查看訂單流程進度', 
                          active_page='order_tracking',
                          orders=orders_list)

@app.route('/admin/order/<int:order_id>/confirm-payment', methods=['POST'])
def admin_confirm_payment(order_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    
    # Check if order exists and payment is made
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    
    if not order:
        conn.close()
        flash('訂單不存在', 'error')
        return redirect(url_for('admin_order_tracking'))
    
    order = dict(order)
    
    if order.get('payment_status') != 'paid':
        conn.close()
        flash('此訂單尚未付款', 'error')
        return redirect(url_for('admin_order_tracking'))
    
    if order.get('payment_confirmed') == 1:
        conn.close()
        flash('此訂單已確認收款', 'info')
        return redirect(url_for('admin_order_tracking'))
    
    # Update payment confirmation
    confirm_time = get_current_time().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('''
        UPDATE orders 
        SET payment_confirmed = 1,
            updated_at = ?
        WHERE id = ?
    ''', (confirm_time, order_id))
    conn.commit()
    conn.close()
    
    # Calculate Unconfirmed Amount (Total Price - Already Recorded Income)
    # This ensures we handle supplements correctly and avoid double counting
    conn = get_db_connection() # ensure connection
    
    # Sum up all 'income' transactions for this order
    existing_income_row = conn.execute('''
        SELECT SUM(amount) as total 
        FROM platform_transactions 
        WHERE related_order_id = ? AND type IN ('income', 'refund')
    ''', (order_id,)).fetchone()
    
    existing_income = existing_income_row['total'] if existing_income_row['total'] else 0
    amount_to_add = order['total_price'] - existing_income
    
    if amount_to_add > 0:
        print(f"DEBUG: Admin Confirm Payment - Adding Amount: {amount_to_add}, Order: {order_id}")
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
    return redirect(url_for('admin_order_tracking'))


@app.route('/admin/complaints')
def admin_complaints():
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    return render_template('admin_generic.html', title='申訴查詢', active_page='complaints')

@app.route('/admin/trainer-review')
def admin_trainer_review():
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    applications = conn.execute('''
        SELECT ta.*, u.name, u.email 
        FROM trainer_applications ta 
        JOIN users u ON ta.user_id = u.id 
        WHERE ta.status = 'pending'
    ''').fetchall()
    conn.close()
    
    return render_template('admin_trainer_review_list.html', applications=applications, active_page='trainer_review')

@app.route('/admin/trainer-review/<int:app_id>')
def admin_trainer_review_detail(app_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    application = conn.execute('''
        SELECT ta.*, u.name, u.email, u.nickname, u.gender, u.dob, u.city, u.district, u.phone
        FROM trainer_applications ta 
        JOIN users u ON ta.user_id = u.id 
        WHERE ta.id = ?
    ''', (app_id,)).fetchone()
    conn.close()
    
    if application is None:
        flash('找不到該申請', 'error')
        return redirect(url_for('admin_trainer_review'))
        
    return render_template('admin_trainer_review_detail.html', app=application, active_page='trainer_review')

@app.route('/admin/trainer-review/<int:app_id>/approve', methods=['POST'])
def admin_approve_application(app_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
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
        
    conn.close()
    return redirect(url_for('admin_trainer_review'))

@app.route('/admin/trainer-review/<int:app_id>/reject', methods=['POST'])
def admin_reject_application(app_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    conn.execute("UPDATE trainer_applications SET status = 'rejected' WHERE id = ?", (app_id,))
    conn.commit()
    conn.close()
    
    flash('已駁回申請', 'info')
    return redirect(url_for('admin_trainer_review'))

# Admin Refund Management
# Admin Financial Management (Refunds & Supplements)
@app.route('/admin/refunds')
def admin_refunds():
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    
    trip_date = request.args.get('trip_date')
    status = request.args.get('status', 'supplement')  # Default to 'supplement'
    
    conn = get_db_connection()
    
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
        
    conn.close()
    
    return render_template('admin_refunds.html', tasks=tasks, active_page='refunds', trip_date=trip_date, status=status)

@app.route('/admin/refunds/<int:task_id>/process', methods=['POST'])
def admin_process_refund(task_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    
    # Fetch task details for balance update
    task = conn.execute('SELECT * FROM refund_tasks WHERE id = ?', (task_id,)).fetchone()
    if not task:
        conn.close()
        flash('找不到該退款任務', 'error')
        return redirect(url_for('admin_refunds'))
        
    try:
        # Mark task as processed
        conn.execute('''
            UPDATE refund_tasks 
            SET status = 'processed', processed_by = ?, processed_at = datetime('now', 'localtime')
            WHERE id = ?
        ''', (session['user_id'], task_id))
        
        # Mark order as refunded (Sync status) AND Reduce paid_amount
        # This is crucial for correct supplement calculation later: paid_amount should reflect NET payment.
        conn.execute('''
            UPDATE orders 
            SET payment_status = 'refunded', 
                paid_amount = MAX(0, IFNULL(paid_amount, 0) - ?),
                updated_at = datetime('now', 'localtime') 
            WHERE id = ?
        ''', (task['amount'], task['order_id']))
        
        conn.commit()
        conn.close() # Close transaction first
        
        # Update Platform Balance (Refund Expense)
        print(f"DEBUG: UI Admin Refund - Amount: {-task['amount']}, Order: {task['order_id']}")
        update_platform_balance(-task['amount'], 'refund', f'訂單 #{task["order_id"]} 退款', order_id=task['order_id'])
        
        flash('退款任務已標記為完成，並已從平台餘額扣除款項', 'success')
    except Exception as e:
        if conn: conn.close()
        flash(f'處理失敗: {str(e)}', 'error')
        
    return redirect(url_for('admin_refunds'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# Trainer Routes


@app.route('/trainer/create-listing', methods=('GET', 'POST'))
def trainer_create_listing():
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
        
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
            conn = get_db_connection()
            cursor = conn.cursor()
            
            saved_filenames = []
            cover_image = None
            
            for i, file in enumerate(files):
                if file.filename:
                     filename = f"listing_{session['user_id']}_{pet_type}_{i}_{file.filename}"
                     file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
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
                 
            conn.close()
            return redirect(url_for('trainer_manage_listings'))
            
    return render_template('trainer_create_listing.html', title='刊登', active_page='create_listing')

@app.route('/trainer/<int:trainer_id>/listings')
def trainer_public_listings(trainer_id):
    conn = get_db_connection()
    
    # Get trainer info
    trainer = conn.execute('SELECT name, nickname, avatar FROM users WHERE id = ?', (trainer_id,)).fetchone()
    
    if not trainer:
        conn.close()
        flash('找不到該訓練師', 'error')
        return redirect(url_for('index'))
        
    # Get approved listings
    listings = conn.execute('''
        SELECT * FROM listings 
        WHERE trainer_id = ? AND status = 'approved'
        ORDER BY created_at DESC
    ''', (trainer_id,)).fetchall()
    
    conn.close()
    
    page_title = f"{trainer['nickname']} 的所有刊登"
    return render_template('index.html', listings=listings, page_title=page_title)

@app.route('/trainer/manage-listings')
def trainer_manage_listings():
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    listings = conn.execute('SELECT * FROM listings WHERE trainer_id = ? ORDER BY created_at DESC', (session['user_id'],)).fetchall()
    conn.close()
    
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

@app.route('/trainer/listing/<int:listing_id>/close', methods=['POST'])
def trainer_close_listing(listing_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    # Verify ownership
    listing = conn.execute('SELECT * FROM listings WHERE id = ? AND trainer_id = ?', (listing_id, session['user_id'])).fetchone()
    if listing and listing['status'] == 'approved':
        conn.execute("UPDATE listings SET status = 'closed' WHERE id = ?", (listing_id,))
        conn.commit()
        flash('刊登已下架', 'success')
    
    conn.close()
    return redirect(url_for('trainer_manage_listings'))

@app.route('/trainer/listing/<int:listing_id>/republish', methods=['POST'])
def trainer_republish_listing(listing_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    # Verify ownership
    listing = conn.execute('SELECT * FROM listings WHERE id = ? AND trainer_id = ?', (listing_id, session['user_id'])).fetchone()
    if listing and listing['status'] == 'closed':
        conn.execute("UPDATE listings SET status = 'approved' WHERE id = ?", (listing_id,))
        conn.commit()
        flash('刊登已重新上架', 'success')
    
    conn.close()
    return redirect(url_for('trainer_manage_listings'))

@app.route('/trainer/listing/<int:listing_id>/cancel-modification', methods=['POST'])
def trainer_cancel_modification(listing_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
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
    
    conn.close()
    return redirect(url_for('trainer_manage_listings'))

@app.route('/trainer/listing/<int:listing_id>/delete', methods=['POST'])
def trainer_delete_listing(listing_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
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
    
    conn.close()
    return redirect(url_for('trainer_manage_listings'))

@app.route('/trainer/listing/<int:listing_id>')
def trainer_listing_detail(listing_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    # Verify ownership
    listing = conn.execute('SELECT * FROM listings WHERE id = ? AND trainer_id = ?', (listing_id, session['user_id'])).fetchone()
    
    if listing:
        # Fetch other images
        images = conn.execute('SELECT filename FROM listing_images WHERE listing_id = ? ORDER BY id ASC', (listing_id,)).fetchall()
        additional_images = [img['filename'] for img in images]
    else:
        additional_images = []
        
    conn.close()
    
    if listing is None:
        flash('找不到該刊登', 'error')
        return redirect(url_for('trainer_manage_listings'))
        
    return render_template('trainer_listing_detail.html', listing=listing, images=additional_images, active_page='manage_listings')

@app.route('/trainer/listing/<int:listing_id>/edit', methods=('GET', 'POST'))
def trainer_listing_edit(listing_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    listing = conn.execute('SELECT * FROM listings WHERE id = ? AND trainer_id = ?', (listing_id, session['user_id'])).fetchone()
    
    if listing is None:
        conn.close()
        flash('找不到該刊登', 'error')
        return redirect(url_for('trainer_manage_listings'))

    # Allow editing approved listings, but they will be set to modification_pending
    # if listing['status'] == 'approved':
    #    ... (removed check)

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
                     # Generate unique filename
                     import uuid
                     filename = f"listing_{session['user_id']}_{listing['pet_type']}_{uuid.uuid4().hex[:8]}_{file.filename}"
                     file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                     saved_new_filenames.append(filename)
                     
                     conn.execute('INSERT INTO listing_images (listing_id, filename) VALUES (?, ?)', (listing_id, filename))
        
        # Handle Deletions
        delete_filenames = request.form.getlist('delete_images')
        if delete_filenames:
            for filename in delete_filenames:
                conn.execute('DELETE FROM listing_images WHERE listing_id = ? AND filename = ?', (listing_id, filename))
                # Optionally delete file from disk here
                try:
                    os.remove(os.path.join(app.config['UPLOAD_FOLDER'], filename))
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
        else:
            pass 
                
        conn.commit()
        conn.close()
        
        flash('修改申請已送出，請等待管理員審核。', 'success')
        return redirect(url_for('trainer_manage_listings'))
    
    # Fetch images for display
    images = conn.execute('SELECT filename FROM listing_images WHERE listing_id = ? ORDER BY id ASC', (listing_id,)).fetchall()
    image_list = [img['filename'] for img in images]
    
    conn.close()
    
    # Parse available_slots for pre-filling form
    # Format: "週一, 週二 09:00 - 18:00"
    import re
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



@app.route('/trainer/calendar')
def trainer_calendar():
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
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
    conn.close()
    
    orders_list = []
    for row in orders_rows:
        o = dict(row)
        # Translation for JS
        if o['status'] == 'confirmed':
            o['status_text'] = '已接受'
        elif o['status'] == 'in_progress':
            o['status_text'] = '進行中'
        elif o['status'] == 'completed':
            o['status_text'] = '已完成'
        else:
            o['status_text'] = o['status']
            
        orders_list.append(o)
    
    import json
    import datetime
    today = datetime.date.today()
    
    return render_template('trainer_calendar.html', 
                           title='行事曆', 
                           active_page='trainer_calendar',
                           orders_json=json.dumps(orders_list),
                           current_year=today.year,
                           current_month=today.month)


@app.route('/trainer/orders')
def trainer_orders():
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
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
    
    conn.close()
    
    # Helper to process order for display
    def process_orders(rows):
        processed = []
        conn = get_db_connection()
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
        conn.close()
        return processed

    pending_orders = process_orders(pending_orders_rows)
    all_history = process_orders(history_orders_rows)
    
    import datetime
    today = datetime.date.today().strftime('%Y-%m-%d')
    
    accepted_orders = []
    ongoing_orders = []
    history_orders = []
    
    for o in all_history:
        if o['status'] == 'confirmed':
             accepted_orders.append(o)
        elif o['status'] == 'in_progress':
             ongoing_orders.append(o)
        else:
            # rejected, cancelled, completed
            # rejected, cancelled, completed
            history_orders.append(o)
    
    print(f"DEBUG: Trainer History loaded {len(history_orders)} items.")
    for h in history_orders:
        print(f" - Order {h['id']}: Status={h['status']}, Payment={h.get('payment_status')}")
    
    return render_template('trainer_orders.html', 
                           title='訂單管理', 
                           active_page='trainer_orders',
                           pending_orders=pending_orders,
                           accepted_orders=accepted_orders,
                           ongoing_orders=ongoing_orders,
                           history_orders=history_orders)

@app.route('/trainer/order/<int:order_id>/<action>', methods=['POST'])
def trainer_order_action(order_id, action):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
        
    if action not in ['accept', 'reject', 'start', 'complete']:
        flash('無效的操作', 'error')
        return redirect(url_for('trainer_orders'))
        
    conn = get_db_connection()
    # Verify ownership
    order = conn.execute('SELECT * FROM orders WHERE id = ? AND seller_id = ?', (order_id, session['user_id'])).fetchone()
    
    if not order:
        conn.close()
        flash('找不到該訂單或無權限', 'error')
        return redirect(url_for('trainer_orders'))
        
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
            conn.close()
            conflict_info = "、".join([f"訂單#{c['id']}（{c['time']}）" for c in conflicts])
            # Store conflict info in session for JavaScript confirmation dialog
            session['pending_accept_order_id'] = order_id
            session['pending_accept_conflicts'] = conflict_info
            flash(f'OVERLAP_WARNING::{order_id}::{conflict_info}', 'warning')
            return redirect(url_for('trainer_orders'))
        
        new_status = 'confirmed'
        msg = '訂單已確認接受！'
        if conflicts:
            msg += '（已略過時段重疊警告）'
    elif action == 'reject':
        new_status = 'rejected'
        msg = '訂單已拒絕'
        flash_type = 'info'
    elif action == 'start':
        # Check if payment is confirmed before allowing start
        order_dict = dict(order)
        if order_dict.get('payment_status') == 'paid' and order_dict.get('payment_confirmed') != 1:
            conn.close()
            flash('此訂單尚未確認收款，無法開始行程。請等待管理員確認收款。', 'error')
            return redirect(url_for('trainer_orders'))
        new_status = 'in_progress'
        msg = '行程已開始！'
    elif action == 'complete':
        new_status = 'completed'
        msg = '行程已完成！'
    else:
        flash('無效的操作', 'error')
        return redirect(url_for('trainer_orders'))
    
    try:
        conn.execute("UPDATE orders SET status = ?, updated_at = datetime('now', 'localtime') WHERE id = ?", (new_status, order_id))
        conn.commit()
        conn.close()
        
        # Process payment to trainer if order is completed
        if action == 'complete':
            if process_order_payment_to_trainer(order_id):
                commission_rate = get_platform_commission_rate()
                msg += f' 款項已發放至您的帳戶（扣除 {commission_rate}% 平台費用）。'
        
        flash(msg, flash_type)
    except Exception as e:
        conn.close()
    return redirect(url_for('trainer_orders'))

@app.route('/api/order/<int:order_id>/reschedule', methods=['POST'])
def api_reschedule_order(order_id):
    if 'user_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    data = request.get_json()
    new_date = data.get('date')
    new_time = data.get('time')
    new_duration = int(data.get('duration', 1))
    
    if not all([new_date, new_time, new_duration]):
        return {'error': 'Missing fields'}, 400
        
    conn = get_db_connection()
    order = conn.execute('SELECT * FROM orders WHERE id = ? AND buyer_id = ?', (order_id, session['user_id'])).fetchone()
    
    if not order:
        conn.close()
        return {'error': 'Order not found'}, 404
        
    order = dict(order)
    
    # Check limit
    resch_count = conn.execute("SELECT COUNT(*) FROM reschedule_requests WHERE order_id = ? AND status = 'approved'", (order_id,)).fetchone()[0]
    if resch_count >= 2:
        conn.close()
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
    except Exception as e:
        print(f"Reschedule error: {e}")
        success = False
        
    conn.close()
    
    if success:
        return {'success': True}
    else:
        return {'error': 'Database error'}, 500

@app.route('/api/order/<int:order_id>/reschedule/respond', methods=['POST'])
def api_respond_reschedule(order_id):
    if 'user_id' not in session or session.get('user_role') != 'trainer':
        return {'error': 'Unauthorized'}, 401
    
    data = request.get_json()
    action = data.get('action') # 'approve' or 'reject'
    
    if action not in ['approve', 'reject']:
        return {'error': 'Invalid action'}, 400
        
    conn = get_db_connection()
    # Verify ownership
    order = conn.execute('SELECT * FROM orders WHERE id = ? AND seller_id = ?', (order_id, session['user_id'])).fetchone()
    
    if not order:
        conn.close()
        return {'error': 'Order not found'}, 404
        
    # Get pending request
    request_row = conn.execute('SELECT * FROM reschedule_requests WHERE order_id = ? AND status = "pending"', (order_id,)).fetchone()
    
    if not request_row:
        conn.close()
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
            
            conn.execute('''
                UPDATE orders 
                SET booking_date = ?, booking_time = ?, duration = ?, total_price = total_price + ?, updated_at = datetime('now', 'localtime')
                WHERE id = ?
            ''', (new_date, new_time, new_duration, price_diff, order_id))
            
            conn.execute('UPDATE reschedule_requests SET status = "approved" WHERE id = ?', (request_row['id'],))
            
            # Handle Price Diff (Refund if negative)
            if price_diff < 0:
                refund_amount = abs(price_diff)
                conn.execute('''
                    INSERT INTO refund_tasks (user_id, order_id, amount, reason, status, created_at)
                    VALUES (?, ?, ?, '改期退款 (時數減少/變更)', 'pending', datetime('now', 'localtime'))
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
        print(f"Response error: {e}")
        conn.rollback()
        success = False
        msg = f'操作失敗: {str(e)}'
        
    conn.close()
    
    if success:
        return {'success': True, 'message': msg}
    else:
        return {'error': msg}, 500

# Bank Account APIs
@app.route('/api/user/bank-accounts', methods=['GET'])
def get_user_bank_accounts():
    if 'user_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    conn = get_db_connection()
    accounts = conn.execute('SELECT * FROM user_bank_accounts WHERE user_id = ? ORDER BY is_default DESC, created_at DESC', (session['user_id'],)).fetchall()
    conn.close()
    
    return {'accounts': [dict(a) for a in accounts]}

@app.route('/api/user/bank-accounts', methods=['POST'])
def add_user_bank_account():
    if 'user_id' not in session:
        return {'error': 'Unauthorized'}, 401
        
    data = request.get_json()
    bank_code = data.get('bank_code')
    account_number = data.get('account_number')
    account_name = data.get('account_name')
    is_default = data.get('is_default', False)
    
    if not all([bank_code, account_number, account_name]):
        return {'error': 'Missing fields'}, 400
        
    conn = get_db_connection()
    if is_default:
        conn.execute('UPDATE user_bank_accounts SET is_default = 0 WHERE user_id = ?', (session['user_id'],))
        
    conn.execute('''
        INSERT INTO user_bank_accounts (user_id, bank_code, account_number, account_name, is_default)
        VALUES (?, ?, ?, ?, ?)
    ''', (session['user_id'], bank_code, account_number, account_name, is_default))
    conn.commit()
    conn.close()
    
    return {'success': True}

@app.route('/api/user/bank-accounts/<int:account_id>', methods=['DELETE'])
def api_delete_user_bank_account(account_id):
    if 'user_id' not in session:
        return {'error': 'Unauthorized'}, 401
        
    conn = get_db_connection()
    conn.execute('DELETE FROM user_bank_accounts WHERE id = ? AND user_id = ?', (account_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return {'success': True}

# Order Cancellation API
@app.route('/api/order/<int:order_id>/cancel', methods=['POST'])
def api_cancel_order(order_id):
    if 'user_id' not in session:
        return {'error': 'Unauthorized'}, 401
        
    conn = get_db_connection()
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
            
        if order['status'] not in ['pending', 'confirmed']:
            return {'error': 'Cannot cancel order in current status'}, 400
            
        # Check Monthly Cancellation Limit
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
            # Fallback for complex time range strings or other errors
            # Only block if we can strictly determine it's past deadline, otherwise allow (safe fail) -> Or block?
            # To be safe for consumers, if we can't parse, we might assume it is check-able manually or allow.
            # But usually we want to block late cancels.
            # Let's try to parse "14:00 - 16:00" style if happens
            try:
                start_part = order['booking_time'].split('-')[0].strip()
                booking_dt = datetime.strptime(f"{order['booking_date']} {start_part}", '%Y-%m-%d %H:%M')
            except:
                print(f"Date parse error: {e}")
                booking_dt = datetime.now() + timedelta(days=999) # Future

        deadline_hours = order['cancellation_hours'] if order['cancellation_hours'] is not None else 24
        deadline_dt = booking_dt - timedelta(hours=deadline_hours)
        
        if datetime.now() > deadline_dt:
            return {'error': f'已超過取消時限（需於 {deadline_hours} 小時前取消）'}, 400
            
        # Handle Refund if Paid
        data = request.get_json() or {}
        
        if order.get('payment_status') == 'paid':
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
            conn.execute('UPDATE orders SET paid_amount = IFNULL(paid_amount, 0) + ? WHERE id = ?', (order['total_price'], order_id)) # Fallback if logic is simple
            # But wait, above logic sends order['total_price'] as amount?
            # We should charge remaining_amount.
            # Let's fix process_payment logic below first.

            # Create Refund Task
            # Calculate amount if missing
            refund_amount = order['total_price']
            if not refund_amount:
                price = order['price'] or 0
                duration = order['duration'] or 1
                refund_amount = price * duration
            
            conn.execute('''
                INSERT INTO refund_tasks (user_id, order_id, amount, reason, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', datetime('now', 'localtime'))
            ''', (session['user_id'], order_id, refund_amount, f"消費者取消: {refund_details}"))
            
        # Update Order
        conn.execute("UPDATE orders SET status = 'cancelled', updated_at = datetime('now', 'localtime') WHERE id = ?", (order_id,))
        conn.commit()
        return {'success': True}

    except Exception as e:
        conn.rollback()
        print(f"Cancel error: {e}")
        return {'error': f'System Error: {str(e)}'}, 500
    finally:
        conn.close()

# Admin Refund APIs
@app.route('/api/admin/refund-task/<int:order_id>', methods=['GET'])
def get_admin_refund_task(order_id):
    if session.get('user_role') != 'admin':
        return {'error': 'Unauthorized'}, 403
        
    conn = get_db_connection()
    task = conn.execute('SELECT * FROM refund_tasks WHERE order_id = ? ORDER BY created_at DESC LIMIT 1', (order_id,)).fetchone()
    conn.close()
    
    if not task:
        return {'error': 'Refund task not found'}, 404
        
    return {'task': dict(task)}

@app.route('/api/admin/refund-task/<int:task_id>/process', methods=['POST'])
def process_admin_refund_task(task_id):
    if session.get('user_role') != 'admin':
        return {'error': 'Unauthorized'}, 403
        
    conn = get_db_connection()
    task = conn.execute('SELECT * FROM refund_tasks WHERE id = ?', (task_id,)).fetchone()
    if not task:
        conn.close()
        return {'error': 'Task not found'}, 404
        
    try:
        # Mark task as processed
        conn.execute('''
            UPDATE refund_tasks 
            SET status = 'processed', 
                processed_by = ?, 
                processed_at = datetime('now', 'localtime') 
            WHERE id = ?
        ''', (session['user_id'], task_id))
        
        # Mark order as refunded AND Reduce paid_amount
        conn.execute('''
            UPDATE orders 
            SET payment_status = 'refunded', 
                paid_amount = MAX(0, IFNULL(paid_amount, 0) - ?),
                updated_at = datetime('now', 'localtime') 
            WHERE id = ?
        ''', (task['amount'], task['order_id']))
        
        # Record transaction (refund expense) & Update Balance
        # conn.execute(...) -> REMOVED manual insert, use helper below
        
        conn.commit()
        conn.close() # Close transaction first
        
        # Update Platform Balance (New Transaction)
        print(f"DEBUG: Processing Refund - Amount: {-task['amount']}, Order: {task['order_id']}")
        update_platform_balance(-task['amount'], 'refund', f'訂單 #{task["order_id"]} 退款', order_id=task['order_id'])
        
        return {'success': True}
    except Exception as e:
        conn.rollback()
        conn.close()
        return {'error': str(e)}, 500
    except Exception as e:
        conn.rollback()
        conn.close()
        return {'error': str(e)}, 500
        
    return {'success': True}

# Messaging API
@app.route('/api/order/<int:order_id>/messages', methods=['GET'])
def get_order_messages(order_id):
    if 'user_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    user_id = session['user_id']
    conn = get_db_connection()
    
    # Verify access
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    if not order or (order['buyer_id'] != user_id and order['seller_id'] != user_id):
        conn.close()
        return {'error': 'Forbidden'}, 403
    
    # Mark messages as read where recipient is current user
    # Identify the pair
    buyer_id = order['buyer_id']
    seller_id = order['seller_id']

    # Mark messages as read where recipient is current user
    # And the message belongs to any order between these two users
    conn.execute('''
        UPDATE order_messages 
        SET is_read = 1 
        WHERE sender_id != ? 
          AND is_read = 0
          AND order_id IN (
              SELECT id FROM orders 
              WHERE (buyer_id = ? AND seller_id = ?) 
                 OR (buyer_id = ? AND seller_id = ?)
          )
    ''', (user_id, buyer_id, seller_id, seller_id, buyer_id))
    conn.commit()
    
    messages = conn.execute('''
        SELECT m.*, 
               CASE WHEN u.nickname IS NOT NULL AND u.nickname != '' THEN u.nickname ELSE u.name END as sender_name,
               u.avatar as sender_avatar
        FROM order_messages m
        JOIN users u ON m.sender_id = u.id
        JOIN orders o ON m.order_id = o.id
        WHERE (o.buyer_id = ? AND o.seller_id = ?) 
           OR (o.buyer_id = ? AND o.seller_id = ?)
        ORDER BY m.created_at ASC
    ''', (buyer_id, seller_id, seller_id, buyer_id)).fetchall()
    
    conn.close()
    return {'messages': [dict(m) for m in messages], 'current_user_id': user_id}

@app.route('/api/order/<int:order_id>/messages', methods=['POST'])
def send_order_message(order_id):
    if 'user_id' not in session:
        return {'error': 'Unauthorized'}, 401
        
    data = request.get_json()
    message = data.get('message', '').strip()
    if not message:
        return {'error': 'Empty message'}, 400
        
    conn = get_db_connection()
    # Verify participant
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    if not order or (order['buyer_id'] != session['user_id'] and order['seller_id'] != session['user_id']):
        conn.close()
        return {'error': 'Unauthorized'}, 403
        
    # Check Blacklist
    sender_id = session['user_id']
    recipient_id = order['seller_id'] if order['buyer_id'] == sender_id else order['buyer_id']
    
    # Check if sender is blocked by recipient
    # Trainer blocks Consumer. If sender is consumer (buyer), need to check if trainer (seller) blocked them.
    # If sender is trainer, we assume they can speak (or maybe we shouldn't rely on that, but usually blocking is "prevent other from contacting me")
    # Requirement: "consumer... cannot contact".
    
    if order['buyer_id'] == sender_id: # Sender is consumer
        is_blocked = conn.execute('SELECT 1 FROM blacklists WHERE trainer_id = ? AND consumer_id = ?', (order['seller_id'], sender_id)).fetchone()
        if is_blocked:
            conn.close()
            return {'error': '您已被此訓練師封鎖，無法傳送訊息。'}, 403
        
    conn.execute('INSERT INTO order_messages (order_id, sender_id, message, created_at) VALUES (?, ?, ?, datetime("now", "localtime"))',
                 (order_id, session['user_id'], message))
    conn.commit()
    conn.close()
    
    return {'success': True}

@app.route('/api/chat/context')
def get_chat_context():
    if 'user_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    target_user_id = request.args.get('user_id')
    if not target_user_id:
        return {'error': 'Missing user_id'}, 400
        
    current_user_id = session['user_id']
    conn = get_db_connection()
    
    # Find the most relevant order to use as chat context
    # Prioritize orders with messages, then latest created
    # Exclude inquiry orders *unless* there are messages on them (then they are valid contexts)
    # Actually, we can just fetch the latest one.
    query = '''
        SELECT 
            o.id as order_id,
            (SELECT MAX(created_at) FROM order_messages WHERE order_id = o.id) as msg_time
        FROM orders o
        WHERE (o.buyer_id = ? AND o.seller_id = ?) 
           OR (o.buyer_id = ? AND o.seller_id = ?)
        ORDER BY msg_time DESC, o.created_at DESC
        LIMIT 1
    '''
    
    row = conn.execute(query, (current_user_id, target_user_id, target_user_id, current_user_id)).fetchone()
    
    if row:
        conn.close()
        return {'order_id': row['order_id']}
    
    # If no existing order, create a new inquiry order IF listing_id is provided
    listing_id = request.args.get('listing_id')
    
    if not listing_id:
        conn.close()
        return {'order_id': None}
        
    # Get listing info to verify and calculate price (though price might be 0 for inquiry)
    listing = conn.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
    if not listing:
        conn.close()
        return {'error': 'Listing not found'}, 404

    # Create inquiry order
    # Using 'inquiry' status. booking_date/time are required NOT NULL, so use dummy values.
    # Dummy values: today, 00:00
    today_str = datetime.now().strftime('%Y-%m-%d')
    dummy_time = '00:00'
    
    # We'll set the status to 'inquiry'
    try:
        cur = conn.execute('''
            INSERT INTO orders 
            (listing_id, buyer_id, seller_id, booking_date, booking_time, status, price_at_booking, total_price, duration)
            VALUES (?, ?, ?, ?, ?, 'inquiry', ?, 0, 1)
        ''', (listing_id, current_user_id, target_user_id, today_str, dummy_time, listing['price']))
        new_order_id = cur.lastrowid
        conn.commit()
        conn.close()
        return {'order_id': new_order_id}
    except Exception as e:
        conn.close()
        print(f"Error creating inquiry order: {e}")
        return {'error': 'Failed to create inquiry context'}, 500

@app.route('/api/chat/unread_count')
def get_global_unread_count():
    if 'user_id' not in session:
        return {'count': 0}
    
    user_id = session['user_id']
    conn = get_db_connection()
    
    # Count unread messages where current user is the recipient (buyer or seller)
    # Message sender != current user
    # Order involves current user
    query = '''
        SELECT COUNT(*) 
        FROM order_messages m 
        JOIN orders o ON m.order_id = o.id 
        WHERE m.sender_id != ? 
          AND m.is_read = 0 
          AND (o.buyer_id = ? OR o.seller_id = ?)
    '''
    
    count = conn.execute(query, (user_id, user_id, user_id)).fetchone()[0]
    conn.close()
    
    return {'count': count}

# Update get_chat_contacts to include unread count
@app.route('/api/chat/contacts')
def get_chat_contacts():
    if 'user_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    user_id = session['user_id']
    conn = get_db_connection()
    
    # Use CTE to find the distinct contacts and aggregate their stats across ALL shared orders
    query = '''
        WITH Contacts AS (
            SELECT DISTINCT 
                CASE WHEN buyer_id = ? THEN seller_id ELSE buyer_id END as contact_id
            FROM orders
            WHERE buyer_id = ? OR seller_id = ?
        ),
        ContactStats AS (
            SELECT 
                c.contact_id,
                -- Get the latest message content across all shared orders
                (
                    SELECT message 
                    FROM order_messages m 
                    JOIN orders o ON m.order_id = o.id 
                    WHERE (o.buyer_id = ? AND o.seller_id = c.contact_id) 
                       OR (o.buyer_id = c.contact_id AND o.seller_id = ?)
                    ORDER BY m.created_at DESC LIMIT 1
                ) as last_content,
                -- Get the latest message time across all shared orders
                (
                    SELECT MAX(m.created_at) 
                    FROM order_messages m 
                    JOIN orders o ON m.order_id = o.id 
                    WHERE (o.buyer_id = ? AND o.seller_id = c.contact_id) 
                       OR (o.buyer_id = c.contact_id AND o.seller_id = ?)
                ) as msg_time,
                -- Sum unread counts across all shared orders
                (
                    SELECT COUNT(*) 
                    FROM order_messages m 
                    JOIN orders o ON m.order_id = o.id 
                    WHERE m.sender_id = c.contact_id 
                      AND m.is_read = 0
                      AND ((o.buyer_id = ? AND o.seller_id = c.contact_id) OR (o.buyer_id = c.contact_id AND o.seller_id = ?))
                ) as unread_count,
                 -- Get the latest order ID (for clicking)
                (
                    SELECT id 
                    FROM orders o
                    WHERE ((o.buyer_id = ? AND o.seller_id = c.contact_id) OR (o.buyer_id = c.contact_id AND o.seller_id = ?))
                    ORDER BY created_at DESC LIMIT 1
                ) as last_order_id
            FROM Contacts c
        )
        SELECT 
            u.id as user_id,
            CASE WHEN u.nickname IS NOT NULL AND u.nickname != '' THEN u.nickname ELSE u.name END as name,
            cs.last_order_id,
            cs.last_content as last_message,
            cs.msg_time as last_message_time,
            cs.unread_count
        FROM ContactStats cs
        JOIN users u ON cs.contact_id = u.id
        ORDER BY cs.msg_time DESC
    '''
    
    # 11 params total: 3 for Contacts, 2 for content, 2 for time, 2 for unread, 2 for order_id
    params = (user_id,) * 11
    
    try:
        contacts_rows = conn.execute(query, params).fetchall()
        contacts = [dict(row) for row in contacts_rows]
    except Exception as e:
        print(f"Error fetching contacts: {e}")
        contacts = []
    
    conn.close()
    return {'contacts': contacts}

@app.route('/admin/member-management')
def admin_member_management():
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    
    query = request.args.get('q', '').strip()
    role_filter = request.args.get('role', 'all')
    
    conn = get_db_connection()
    
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
    
    conn.close()
    
    return render_template('admin_member_list.html', 
                           users=users, 
                           search_query=query, 
                           current_role=role_filter,
                           active_page='member_management')
    
@app.route('/admin/statistics')
def admin_statistics_dashboard():
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    
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
    
    conn.close()
    
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

@app.route('/admin/member-management/<int:user_id>')
def admin_member_detail(user_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    # Connection closed later
    
    if user is None:
        flash('找不到該會員', 'error')
        return redirect(url_for('admin_member_management'))
    
    # Fetch level history if user is a trainer
    level_history = []
    if user['role'] == 'trainer':
        level_history = conn.execute('''
            SELECT * FROM trainer_level_history 
            WHERE trainer_id = ? 
            ORDER BY created_at DESC
        ''', (user_id,)).fetchall()
        
    conn.close() # Close connection after all queries
        
    return render_template('admin_member_detail.html', user=user, level_history=level_history, active_page='member_management')

@app.route('/admin/member-management/<int:user_id>/edit', methods=('GET', 'POST'))
def admin_member_edit(user_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
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
            return redirect(url_for('admin_member_detail', user_id=user_id))
        except sqlite3.IntegrityError:
             flash('Email 已被使用', 'error')
        except Exception as e:
            flash(f'更新失敗: {e}', 'error')
        finally:
            conn.close()
            
    # GET request
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    
    if user is None:
        return redirect(url_for('admin_member_management'))
        
    return render_template('admin_member_edit.html', user=user, active_page='member_management')

@app.route('/admin/member-management/<int:user_id>/delete', methods=['POST'])
def admin_member_delete(user_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    try:
        # Delete related records first (optional, but good practice. For now just users table or let cascade handle if set, but sqlite needs pragma. 
        # We should delete trainer application if exists)
        conn.execute('DELETE FROM trainer_applications WHERE user_id = ?', (user_id,))
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        flash('會員已刪除', 'success')
    except Exception as e:
        flash(f'刪除失敗: {e}', 'error')
    finally:
        conn.close()
        
    return redirect(url_for('admin_member_management'))

@app.route('/admin/appeals')
def admin_appeals():
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    appeals = conn.execute('''
        SELECT a.*, o.booking_date, o.booking_time, u.name as user_name, u.email as user_email
        FROM order_appeals a
        JOIN orders o ON a.order_id = o.id
        JOIN users u ON a.user_id = u.id
        ORDER BY a.created_at DESC
    ''').fetchall()
    conn.close()
    
    return render_template('admin_appeals_list.html', appeals=appeals, active_page='appeals', title='申訴案件管理')

@app.route('/admin/appeal/<int:appeal_id>', methods=('GET', 'POST'))
def admin_appeal_detail(appeal_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    
    if request.method == 'POST':
        action = request.form.get('action')
        message = request.form.get('admin_reply', '').strip()
        
        # Handle message sending (reply_only)
        if action == 'reply_only':
            if message:
                conn.execute('''
                    INSERT INTO appeal_messages (appeal_id, sender_id, sender_role, message, created_at)
                    VALUES (?, ?, 'admin', ?, datetime('now', 'localtime'))
                ''', (appeal_id, session['user_id'], message))
                conn.commit()
            flash('留言已送出', 'success')
            conn.close()
            return redirect(url_for('admin_appeal_detail', appeal_id=appeal_id))
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
                conn.execute('''
                    INSERT INTO appeal_messages (appeal_id, sender_id, sender_role, message, created_at)
                    VALUES (?, ?, 'admin', ?, datetime('now', 'localtime'))
                ''', (appeal_id, session['user_id'], message))
                
            conn.execute('''
                UPDATE order_appeals 
                SET status = ?, updated_at = datetime('now', 'localtime')
                WHERE id = ?
            ''', (status, appeal_id))
            conn.commit()
            flash('案件狀態已更新', 'success')
            conn.close()
            return redirect(url_for('admin_appeals'))
        
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
        conn.close()
        return redirect(url_for('admin_appeals'))
    
    # Fetch all messages
    messages = conn.execute('''
        SELECT m.*, u.name as sender_name, u.nickname as sender_nickname
        FROM appeal_messages m
        JOIN users u ON m.sender_id = u.id
        WHERE m.appeal_id = ?
        ORDER BY m.created_at ASC
    ''', (appeal_id,)).fetchall()
    
    conn.close()
        
    return render_template('admin_appeal_detail.html', appeal=appeal, messages=messages, active_page='appeals', title='案件詳情')

@app.route('/admin/platform-settings', methods=('GET', 'POST'))
def admin_fee_settings():
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    
    if request.method == 'POST':
        try:
            commission_general = float(request.form.get('commission_general', 15.0))
            commission_advanced = float(request.form.get('commission_advanced', 10.0))
            commission_professional = float(request.form.get('commission_professional', 5.0))
            level_adv_threshold = float(request.form.get('level_adv_threshold', 4.0))
            level_pro_threshold = float(request.form.get('level_pro_threshold', 4.5))
            
            # Legacy field update just in case
            commission_rate = commission_general 
            
            conn.execute('''
                UPDATE platform_settings 
                SET commission_rate = ?, 
                    commission_general = ?, commission_advanced = ?, commission_professional = ?,
                    level_adv_threshold = ?, level_pro_threshold = ?,
                    updated_at = datetime('now', 'localtime')
                WHERE id = 1
            ''', (commission_rate, commission_general, commission_advanced, commission_professional, 
                  level_adv_threshold, level_pro_threshold))
            conn.commit()
            flash('平台設定已更新', 'success')
        except ValueError:
            flash('請輸入有效的數值', 'error')
    
    settings = conn.execute('SELECT * FROM platform_settings LIMIT 1').fetchone()
    conn.close()
    
    return render_template('admin_platform_settings.html',
                          title='平台相關設定',
                          active_page='fee_settings',
                          settings=dict(settings) if settings else {})

# Admin Withdrawal Management Routes
@app.route('/admin/withdrawal-requests')
def admin_withdrawal_requests():
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    
    # Get all withdrawal requests with user info
    requests = conn.execute('''
        SELECT w.*, u.name as trainer_name, u.nickname as trainer_nickname,
               t.balance as current_balance
        FROM withdrawal_requests w
        JOIN users u ON w.user_id = u.id
        LEFT JOIN trainer_accounts t ON w.user_id = t.user_id
        ORDER BY w.created_at DESC
    ''').fetchall()
    
    conn.close()
    
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

@app.route('/admin/withdrawal/<int:request_id>/approve', methods=['POST'])
def admin_approve_withdrawal(request_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    
    # Get withdrawal request
    withdrawal = conn.execute('SELECT * FROM withdrawal_requests WHERE id = ?', 
                             (request_id,)).fetchone()
    
    if not withdrawal:
        conn.close()
        flash('找不到該提領申請', 'error')
        return redirect(url_for('admin_withdrawal_requests'))
    
    if withdrawal['status'] != 'pending':
        conn.close()
        flash('此申請已處理過', 'error')
        return redirect(url_for('admin_withdrawal_requests'))
    
    # Get trainer account
    account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?',
                          (withdrawal['user_id'],)).fetchone()
    
    if not account or account['balance'] < withdrawal['amount']:
        conn.close()
        flash('訓練師餘額不足，無法核准提領', 'error')
        return redirect(url_for('admin_withdrawal_requests'))
    
    try:
        # Update account balance
        new_balance = account['balance'] - withdrawal['amount']
        conn.execute('''
            UPDATE trainer_accounts 
            SET balance = ?, updated_at = datetime('now', 'localtime')
            WHERE user_id = ?
        ''', (new_balance, withdrawal['user_id']))

        # Record transaction
        conn.execute('''
            INSERT INTO account_transactions 
            (user_id, amount, type, description, created_at)
            VALUES (?, ?, 'withdrawal', ?, datetime('now', 'localtime'))
        ''', (withdrawal['user_id'], -withdrawal['amount'], 
              f'提領申請 #{request_id} 已核准'))
        
        # Update withdrawal request status
        conn.execute('''
            UPDATE withdrawal_requests
            SET status = 'approved', 
                processed_at = datetime('now', 'localtime'),
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
    
    conn.close()
    return redirect(url_for('admin_withdrawal_requests'))

@app.route('/admin/withdrawal/<int:request_id>/reject', methods=['POST'])
def admin_reject_withdrawal(request_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    
    withdrawal = conn.execute('SELECT * FROM withdrawal_requests WHERE id = ?',
                             (request_id,)).fetchone()
    
    if not withdrawal:
        conn.close()
        flash('找不到該提領申請', 'error')
        return redirect(url_for('admin_withdrawal_requests'))
    
    if withdrawal['status'] != 'pending':
        conn.close()
        flash('此申請已處理過', 'error')
        return redirect(url_for('admin_withdrawal_requests'))
    
    admin_note = request.form.get('admin_note', '')
    
    conn.execute('''
        UPDATE withdrawal_requests
        SET status = 'rejected',
            admin_note = ?,
            processed_at = datetime('now', 'localtime'),
            processed_by = ?
        WHERE id = ?
    ''', (admin_note, session['user_id'], request_id))
    
    conn.commit()
    conn.close()
    
    flash('已拒絕提領申請', 'info')
    return redirect(url_for('admin_withdrawal_requests'))

@app.route('/trainer/level-history')
def trainer_level_history():
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    
    level_history = conn.execute('''
        SELECT * FROM trainer_level_history 
        WHERE trainer_id = ? 
        ORDER BY created_at DESC
    ''', (session['user_id'],)).fetchall()
    
    conn.close()
    
    return render_template('trainer_level_history.html', 
                          level_history=level_history, 
                          active_page='trainer_account')

# Trainer Account Routes
@app.route('/trainer/account')
def trainer_account():
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))

    conn = get_db_connection()

    # Get or create account
    account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?',
                          (session['user_id'],)).fetchone()

    if not account:
        conn.execute('''
            INSERT INTO trainer_accounts (user_id, balance, total_earned, created_at, updated_at)
            VALUES (?, 0.0, 0.0, datetime('now', 'localtime'), datetime('now', 'localtime'))
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
    
    # Calculate running balance
    # Note: To show correct running balance with filtering, we need all transactions first
    # Or we can recalculate from the total earned/withdrawn?
    # Simplest accurate way: Get all transactions to compute balances, THEN filter for display
    
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
    current_level = account['current_level'] if 'current_level' in account.keys() and account['current_level'] else 'general'
    
    # Determine current rate for display
    current_rate = settings.get('commission_general', 15.0)
    if current_level == 'professional':
        current_rate = settings.get('commission_professional', 5.0)
    elif current_level == 'advanced':
        current_rate = settings.get('commission_advanced', 10.0)
    
    # Get withdrawal requests for this trainer
    withdrawal_requests = conn.execute('''
        SELECT * FROM withdrawal_requests 
        WHERE user_id = ?
        ORDER BY created_at DESC
    ''', (session['user_id'],)).fetchall()
    withdrawal_requests = [dict(r) for r in withdrawal_requests]
        
    conn.close()

    return render_template('trainer_account.html',
                          title='平台帳戶',
                          active_page='trainer_account',
                          account=dict(account),
                          transactions=transactions,
                          current_level=current_level,
                          commission_rate=current_rate,
                          selected_date=filter_date,
                          withdrawal_requests=withdrawal_requests)

@app.route('/trainer/withdrawal', methods=['GET', 'POST'])
def trainer_withdrawal():
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    
    # Get account balance
    account = conn.execute('SELECT * FROM trainer_accounts WHERE user_id = ?', 
                          (session['user_id'],)).fetchone()
    
    if not account:
        conn.close()
        flash('請先完成訂單以建立帳戶', 'error')
        return redirect(url_for('trainer_account'))
    
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
                        conn.execute('''
                            INSERT INTO trainer_bank_accounts (user_id, bank_code, bank_account, account_holder, created_at)
                            VALUES (?, ?, ?, ?, datetime('now', 'localtime'))
                        ''', (session['user_id'], bank_code, bank_account, account_holder))
                
                # Create withdrawal request
                conn.execute('''
                    INSERT INTO withdrawal_requests 
                    (user_id, amount, bank_name, bank_account, account_holder, status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', datetime('now', 'localtime'))
                ''', (session['user_id'], amount, bank_code, bank_account, account_holder))
                
                conn.commit()
                flash('提領申請已送出，請等待審核', 'success')
                return redirect(url_for('trainer_account'))
                
        except ValueError:
            flash('請輸入有效的金額', 'error')

    # Get saved bank accounts
    saved_accounts = conn.execute('''
        SELECT * FROM trainer_bank_accounts WHERE user_id = ? ORDER BY created_at DESC
    ''', (session['user_id'],)).fetchall()
    
    conn.close()
    
    return render_template('trainer_withdrawal.html',
                          title='申請提領',
                          active_page='account',
                          account=account,
                          balance=account['balance'],
                          saved_accounts=[dict(row) for row in saved_accounts])


# Review System APIs
@app.route('/api/reviews', methods=['POST'])
def submit_review():
    if 'user_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    data = request.get_json()
    order_id = data.get('order_id')
    rating = data.get('rating')
    comment = data.get('comment', '')
    
    if not order_id or not rating:
        return {'error': 'Missing required fields'}, 400
        
    try:
        rating = int(rating)
        if rating < 1 or rating > 5:
            return {'error': 'Invalid rating'}, 400
    except ValueError:
        return {'error': 'Invalid rating'}, 400

    conn = get_db_connection()
    
    # Verify order and permission
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    if not order:
        conn.close()
        return {'error': 'Order not found'}, 404
        
    if order['status'] != 'completed':
        conn.close()
        return {'error': 'Order must be completed to review'}, 400

    current_user_id = session['user_id']
    
    # Determine Review Type and Target
    if current_user_id == order['buyer_id']:
        # Buyer reviewing Trainer
        target_user_id = order['seller_id']
        listing_id = order['listing_id']
        review_type = 'buyer_to_trainer'
    elif current_user_id == order['seller_id']:
        # Trainer reviewing Buyer
        target_user_id = order['buyer_id']
        listing_id = None # Trainer reviewing buyer doesn't attach to a listing specifically
        review_type = 'trainer_to_buyer'
    else:
        conn.close()
        return {'error': 'Unauthorized'}, 403

    # Check if review already exists
    existing = conn.execute('''
        SELECT id FROM reviews 
        WHERE order_id = ? AND source_user_id = ?
    ''', (order_id, current_user_id)).fetchone()
    
    if existing:
        conn.close()
        return {'error': 'You have already reviewed this order'}, 400

    try:
        conn.execute('''
            INSERT INTO reviews (order_id, source_user_id, target_user_id, listing_id, rating, comment, review_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        ''', (order_id, current_user_id, target_user_id, listing_id, rating, comment, review_type))
        conn.commit()
    except Exception as e:
        conn.close()
        return {'error': str(e)}, 500
        
    conn.close()
    
    # Update trainer level if review target was trainer
    if review_type == 'buyer_to_trainer':
        try:
            update_trainer_level(target_user_id)
        except Exception as e:
            print(f"Failed to update trainer level: {e}")
            
    return {'success': True}

@app.route('/api/users/<int:user_id>/reviews')
def get_user_reviews(user_id):
    # API to fetch reviews RECEIVED by a user (e.g. Trainer checking Buyer's reviews)
    if 'user_id' not in session:
         return {'error': 'Unauthorized'}, 401
         
    # Optional: restricts who can see reviews? For now, public or at least logged in.
    
    conn = get_db_connection()
    query = '''
        SELECT r.*, u.name as reviewer_name, u.nickname as reviewer_nickname, u.avatar as reviewer_avatar
        FROM reviews r
        JOIN users u ON r.source_user_id = u.id
        WHERE r.target_user_id = ?
        ORDER BY r.created_at DESC
    '''
    params = (user_id,)
    reviews = conn.execute(query, params).fetchall()
    conn.close()
    
    return {'reviews': [dict(r) for r in reviews]}

@app.route('/trainer/reviews')
def trainer_reviews_history():
    if session.get('user_role') != 'trainer':
        return redirect(url_for('index'))
        
    filter_date = request.args.get('date')
    conn = get_db_connection()
    
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
    
    conn.close()
    
    return render_template('trainer_reviews.html', 
                          reviews=[dict(r) for r in reviews],
                          avg_rating=avg_rating,
                          total_reviews=total_reviews,
                          filter_date=filter_date,
                          active_page='trainer_reviews')

@app.route('/trainer/blacklist')
def trainer_blacklist():
    if 'user_id' not in session or session.get('user_role') != 'trainer':
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    blacklist = conn.execute('''
        SELECT b.*, u.name, u.nickname, u.email, u.avatar 
        FROM blacklists b
        JOIN users u ON b.consumer_id = u.id
        WHERE b.trainer_id = ?
        ORDER BY b.created_at DESC
    ''', (session['user_id'],)).fetchall()
    conn.close()
    
    return render_template('trainer_blacklist.html', blacklist=blacklist, active_page='blacklist')

@app.route('/api/blacklist/add', methods=['POST'])
def add_to_blacklist():
    if 'user_id' not in session or session.get('user_role') != 'trainer':
        return {'error': 'Unauthorized'}, 401
    
    data = request.get_json()
    consumer_id = data.get('consumer_id')
    
    if not consumer_id:
        return {'error': 'Missing consumer_id'}, 400
        
    conn = get_db_connection()
    try:
        conn.execute('INSERT INTO blacklists (trainer_id, consumer_id) VALUES (?, ?)', (session['user_id'], consumer_id))
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False # Already blacklisted
    except Exception as e:
        print(f"Error adding to blacklist: {e}")
        conn.close()
        return {'error': str(e)}, 500
    
    conn.close()
    return {'success': success}

@app.route('/api/blacklist/remove', methods=['POST'])
def remove_from_blacklist():
    if 'user_id' not in session or session.get('user_role') != 'trainer':
        return {'error': 'Unauthorized'}, 401
    
    data = request.get_json()
    consumer_id = data.get('consumer_id')
    
    if not consumer_id:
        return {'error': 'Missing consumer_id'}, 400
        
    conn = get_db_connection()
    conn.execute('DELETE FROM blacklists WHERE trainer_id = ? AND consumer_id = ?', (session['user_id'], consumer_id))
    conn.commit()
    conn.close()
    return {'success': True}

if __name__ == '__main__':
    app.run(debug=True, port=8000)
