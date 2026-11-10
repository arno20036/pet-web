"""Reviews between buyers and trainers after a completed order."""
from flask import current_app

from pet_platform.constants import OrderStatus, ReviewType
from pet_platform.db import get_db
from pet_platform.services import ServiceError
from pet_platform.services.ledger_service import update_trainer_level
from pet_platform.utils.time import NOW_SQL


def parse_rating(raw):
    try:
        rating = int(raw)
    except ValueError:
        raise ServiceError('Invalid rating')
    if rating < 1 or rating > 5:
        raise ServiceError('Invalid rating')
    return rating


def submit_review(order_id, user_id, rating, comment):
    """Buyer reviews the trainer, or trainer reviews the buyer, once per order.

    Raises ServiceError for rule violations; a failed INSERT propagates as sqlite3.Error.
    """
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    if not order:
        raise ServiceError('Order not found', status=404)
    if order['status'] != OrderStatus.COMPLETED:
        raise ServiceError('Order must be completed to review')

    if user_id == order['buyer_id']:
        target_user_id, listing_id, review_type = order['seller_id'], order['listing_id'], ReviewType.BUYER_TO_TRAINER
    elif user_id == order['seller_id']:
        # A trainer's review of a buyer isn't tied to a listing
        target_user_id, listing_id, review_type = order['buyer_id'], None, ReviewType.TRAINER_TO_BUYER
    else:
        raise ServiceError('Unauthorized', status=403)

    existing = conn.execute('SELECT id FROM reviews WHERE order_id = ? AND source_user_id = ?',
                            (order_id, user_id)).fetchone()
    if existing:
        raise ServiceError('You have already reviewed this order')

    conn.execute(f'''
        INSERT INTO reviews (order_id, source_user_id, target_user_id, listing_id, rating, comment, review_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, {NOW_SQL})
    ''', (order_id, user_id, target_user_id, listing_id, rating, comment, review_type))
    conn.commit()

    if review_type == ReviewType.BUYER_TO_TRAINER:
        try:
            update_trainer_level(target_user_id)
        except Exception:
            current_app.logger.exception('Failed to update trainer level')
