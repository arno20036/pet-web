"""Review APIs."""
from flask import current_app, request, session

from pet_platform.constants import OrderStatus, ReviewType
from pet_platform.db import get_db
from pet_platform.decorators import login_required
from pet_platform.services.ledger_service import update_trainer_level
from pet_platform.utils.time import NOW_SQL

from pet_platform.blueprints.api import bp


# Review System APIs
@bp.route('/api/reviews', methods=['POST'])
@login_required(json=True)
def submit_review():
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

    conn = get_db()
    
    # Verify order and permission
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    if not order:
        return {'error': 'Order not found'}, 404
        
    if order['status'] != OrderStatus.COMPLETED:
        return {'error': 'Order must be completed to review'}, 400

    current_user_id = session['user_id']
    
    # Determine Review Type and Target
    if current_user_id == order['buyer_id']:
        # Buyer reviewing Trainer
        target_user_id = order['seller_id']
        listing_id = order['listing_id']
        review_type = ReviewType.BUYER_TO_TRAINER
    elif current_user_id == order['seller_id']:
        # Trainer reviewing Buyer
        target_user_id = order['buyer_id']
        listing_id = None # Trainer reviewing buyer doesn't attach to a listing specifically
        review_type = ReviewType.TRAINER_TO_BUYER
    else:
        return {'error': 'Unauthorized'}, 403

    # Check if review already exists
    existing = conn.execute('''
        SELECT id FROM reviews 
        WHERE order_id = ? AND source_user_id = ?
    ''', (order_id, current_user_id)).fetchone()
    
    if existing:
        return {'error': 'You have already reviewed this order'}, 400

    try:
        conn.execute(f'''
            INSERT INTO reviews (order_id, source_user_id, target_user_id, listing_id, rating, comment, review_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, {NOW_SQL})
        ''', (order_id, current_user_id, target_user_id, listing_id, rating, comment, review_type))
        conn.commit()
    except Exception as e:
        return {'error': str(e)}, 500

    # Update trainer level if review target was trainer
    if review_type == ReviewType.BUYER_TO_TRAINER:
        try:
            update_trainer_level(target_user_id)
        except Exception:
            current_app.logger.exception('Failed to update trainer level')

    return {'success': True}

@bp.route('/api/users/<int:user_id>/reviews')
@login_required(json=True)
def get_user_reviews(user_id):
    # Reviews RECEIVED by a user (e.g. a trainer checking a buyer); any logged-in user may view
    conn = get_db()
    query = '''
        SELECT r.*, u.name as reviewer_name, u.nickname as reviewer_nickname, u.avatar as reviewer_avatar
        FROM reviews r
        JOIN users u ON r.source_user_id = u.id
        WHERE r.target_user_id = ?
        ORDER BY r.created_at DESC
    '''
    params = (user_id,)
    reviews = conn.execute(query, params).fetchall()
    
    return {'reviews': [dict(r) for r in reviews]}
