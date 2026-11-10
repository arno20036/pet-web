"""Order appeals (complaints) and their message threads."""
from pet_platform.db import get_db
from pet_platform.utils.time import NOW_SQL

# admin form action -> new appeal status (anything else means "processing")
ADMIN_ACTION_STATUS = {'resolve': 'resolved', 'close': 'closed'}


def add_message(appeal_id, sender_id, sender_role, message):
    """Append a message to the thread. Does not commit."""
    get_db().execute(f'''
        INSERT INTO appeal_messages (appeal_id, sender_id, sender_role, message, created_at)
        VALUES (?, ?, ?, ?, {NOW_SQL})
    ''', (appeal_id, sender_id, sender_role, message))


def admin_reply(appeal_id, admin_id, message):
    if message:
        add_message(appeal_id, admin_id, 'admin', message)
        get_db().commit()


def admin_set_status(appeal_id, admin_id, action, message):
    """Change the appeal status, optionally posting the admin's message with it."""
    conn = get_db()
    if message:
        add_message(appeal_id, admin_id, 'admin', message)
    conn.execute(f'''
        UPDATE order_appeals
        SET status = ?, updated_at = {NOW_SQL}
        WHERE id = ?
    ''', (ADMIN_ACTION_STATUS.get(action, 'processing'), appeal_id))
    conn.commit()


def admin_appeal(appeal_id):
    return get_db().execute('''
        SELECT a.*, o.booking_date, o.booking_time, o.total_price,
               u.name as user_name, u.email as user_email, u.phone as user_phone,
               s.name as seller_name, s.email as seller_email
        FROM order_appeals a
        JOIN orders o ON a.order_id = o.id
        JOIN users u ON a.user_id = u.id
        JOIN users s ON o.seller_id = s.id
        WHERE a.id = ?
    ''', (appeal_id,)).fetchone()


def messages(appeal_id):
    return get_db().execute('''
        SELECT m.*, u.name as sender_name, u.nickname as sender_nickname
        FROM appeal_messages m
        JOIN users u ON m.sender_id = u.id
        WHERE m.appeal_id = ?
        ORDER BY m.created_at ASC
    ''', (appeal_id,)).fetchall()
