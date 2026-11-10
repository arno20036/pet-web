"""Review APIs."""
import sqlite3

from flask import request, session

from pet_platform.db import get_db
from pet_platform.decorators import login_required
from pet_platform.services import review_service, ServiceError

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
        review_service.submit_review(order_id, session['user_id'], review_service.parse_rating(rating), comment)
    except ServiceError as e:
        return {'error': e.message}, e.status
    except sqlite3.Error as e:
        return {'error': str(e)}, 500
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
