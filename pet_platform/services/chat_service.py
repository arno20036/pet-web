"""Chat between buyers and trainers (messages are attached to orders)."""
from pet_platform.db import get_db

# Everyone the user shares an order with, with stats aggregated over ALL shared orders.
CONTACTS_SQL = '''
    WITH Contacts AS (
        SELECT DISTINCT
            CASE WHEN buyer_id = :uid THEN seller_id ELSE buyer_id END as contact_id
        FROM orders
        WHERE buyer_id = :uid OR seller_id = :uid
    ),
    ContactStats AS (
        SELECT
            c.contact_id,
            -- latest message content
            (
                SELECT message
                FROM order_messages m
                JOIN orders o ON m.order_id = o.id
                WHERE (o.buyer_id = :uid AND o.seller_id = c.contact_id)
                   OR (o.buyer_id = c.contact_id AND o.seller_id = :uid)
                ORDER BY m.created_at DESC LIMIT 1
            ) as last_content,
            -- latest message time
            (
                SELECT MAX(m.created_at)
                FROM order_messages m
                JOIN orders o ON m.order_id = o.id
                WHERE (o.buyer_id = :uid AND o.seller_id = c.contact_id)
                   OR (o.buyer_id = c.contact_id AND o.seller_id = :uid)
            ) as msg_time,
            -- unread messages from this contact
            (
                SELECT COUNT(*)
                FROM order_messages m
                JOIN orders o ON m.order_id = o.id
                WHERE m.sender_id = c.contact_id
                  AND m.is_read = 0
                  AND ((o.buyer_id = :uid AND o.seller_id = c.contact_id) OR (o.buyer_id = c.contact_id AND o.seller_id = :uid))
            ) as unread_count,
            -- latest shared order (opened when the contact is clicked)
            (
                SELECT id
                FROM orders o
                WHERE ((o.buyer_id = :uid AND o.seller_id = c.contact_id) OR (o.buyer_id = c.contact_id AND o.seller_id = :uid))
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


def list_contacts(user_id):
    return [dict(row) for row in get_db().execute(CONTACTS_SQL, {'uid': user_id}).fetchall()]
