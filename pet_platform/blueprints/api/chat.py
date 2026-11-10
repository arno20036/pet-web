"""Chat / order message APIs."""
from datetime import datetime

from flask import current_app, request, session

from pet_platform.db import get_db
from pet_platform.decorators import login_required
from pet_platform.services import chat_service
from pet_platform.utils.time import NOW_SQL

from pet_platform.blueprints.api import bp


# Messaging API
@bp.route('/api/order/<int:order_id>/messages', methods=['GET'])
@login_required(json=True)
def get_order_messages(order_id):
    user_id = session['user_id']
    conn = get_db()
    
    # Verify access
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    if not order or (order['buyer_id'] != user_id and order['seller_id'] != user_id):
        return {'error': 'Forbidden'}, 403
    
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
    
    return {'messages': [dict(m) for m in messages], 'current_user_id': user_id}

@bp.route('/api/order/<int:order_id>/messages', methods=['POST'])
@login_required(json=True)
def send_order_message(order_id):
    data = request.get_json()
    message = data.get('message', '').strip()
    if not message:
        return {'error': 'Empty message'}, 400
        
    conn = get_db()
    # Verify participant
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    if not order or (order['buyer_id'] != session['user_id'] and order['seller_id'] != session['user_id']):
        return {'error': 'Unauthorized'}, 403
        
    # Check Blacklist
    sender_id = session['user_id']
    recipient_id = order['seller_id'] if order['buyer_id'] == sender_id else order['buyer_id']
    
    # Blacklists are trainer -> consumer: a blocked consumer can't message that trainer,
    # while the trainer can still message the consumer.
    if order['buyer_id'] == sender_id: # Sender is consumer
        is_blocked = conn.execute('SELECT 1 FROM blacklists WHERE trainer_id = ? AND consumer_id = ?', (order['seller_id'], sender_id)).fetchone()
        if is_blocked:
            return {'error': '您已被此訓練師封鎖，無法傳送訊息。'}, 403
        
    conn.execute(f'INSERT INTO order_messages (order_id, sender_id, message, created_at) VALUES (?, ?, ?, {NOW_SQL})',
                 (order_id, session['user_id'], message))
    conn.commit()
    
    return {'success': True}

@bp.route('/api/chat/context')
@login_required(json=True)
def get_chat_context():
    target_user_id = request.args.get('user_id')
    if not target_user_id:
        return {'error': 'Missing user_id'}, 400
        
    current_user_id = session['user_id']
    conn = get_db()
    
    # Most relevant order between the two users: latest message first, then latest created
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
        return {'order_id': row['order_id']}
    
    # If no existing order, create a new inquiry order IF listing_id is provided
    listing_id = request.args.get('listing_id')
    
    if not listing_id:
        return {'order_id': None}
        
    # Get listing info to verify and calculate price (though price might be 0 for inquiry)
    listing = conn.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
    if not listing:
        return {'error': 'Listing not found'}, 404

    # Create an 'inquiry' order. booking_date/time are NOT NULL, so use dummy values (today, 00:00).
    today_str = datetime.now().strftime('%Y-%m-%d')
    dummy_time = '00:00'

    try:
        cur = conn.execute('''
            INSERT INTO orders 
            (listing_id, buyer_id, seller_id, booking_date, booking_time, status, price_at_booking, total_price, duration)
            VALUES (?, ?, ?, ?, ?, 'inquiry', ?, 0, 1)
        ''', (listing_id, current_user_id, target_user_id, today_str, dummy_time, listing['price']))
        new_order_id = cur.lastrowid
        conn.commit()
        return {'order_id': new_order_id}
    except Exception:
        current_app.logger.exception('Error creating inquiry order')
        return {'error': 'Failed to create inquiry context'}, 500

@bp.route('/api/chat/unread_count')
def get_global_unread_count():
    if 'user_id' not in session:
        return {'count': 0}
    
    user_id = session['user_id']
    conn = get_db()
    
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
    
    return {'count': count}

@bp.route('/api/chat/contacts')
@login_required(json=True)
def get_chat_contacts():
    try:
        contacts = chat_service.list_contacts(session['user_id'])
    except Exception:
        current_app.logger.exception('Error fetching contacts')
        contacts = []
    return {'contacts': contacts}
