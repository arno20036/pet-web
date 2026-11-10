"""Template context shared by every page (sidebar badge counts)."""
from flask import current_app, session

from pet_platform.constants import Role
from pet_platform.db import get_db


def inject_sidebar_counts():
    counts = {}
    if 'user_id' in session:
        user_id = session['user_id']
        role = session.get('user_role')
        
        try:
            conn = get_db()
            
            if role == Role.TRAINER:
                # Count Pending Orders (Wait for Accept)
                pending_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE seller_id = ? AND status = 'pending'", (user_id,)).fetchone()[0]
                
                # Count Confirmed Orders (Accepted, Wait for Start)
                confirmed_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE seller_id = ? AND status = 'confirmed'", (user_id,)).fetchone()[0]
                
                counts['trainer_pending_orders'] = pending_orders
                counts['trainer_confirmed_orders'] = confirmed_orders
                
                # Calendar Badge: Accepted (confirmed) and Ongoing (in_progress)
                ongoing_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE seller_id = ? AND status = 'in_progress'", (user_id,)).fetchone()[0]
                counts['trainer_calendar_count'] = confirmed_orders + ongoing_orders
                
                # Order Management badge: orders still waiting on the trainer (pending + confirmed)
                counts['trainer_badge_count'] = pending_orders + confirmed_orders
                
            if role == Role.ADMIN:
                # Count Pending Listings
                pending_listings = conn.execute("SELECT COUNT(*) FROM listings WHERE status = 'pending'").fetchone()[0]
                counts['admin_pending_listings'] = pending_listings

                # Count Pending Trainer Applications
                pending_trainers = conn.execute("SELECT COUNT(*) FROM trainer_applications WHERE status = 'pending'").fetchone()[0]
                counts['admin_pending_trainers'] = pending_trainers

                # Count Pending Withdrawal Requests
                pending_withdrawals = conn.execute("SELECT COUNT(*) FROM withdrawal_requests WHERE status = 'pending'").fetchone()[0]
                counts['admin_pending_withdrawals'] = pending_withdrawals
                
                # Paid by buyer but not yet confirmed by admin. Paid supplements also match this,
                # so they are counted both here (Order Tracking) and in the Refunds badge below.
                pending_payments = conn.execute("SELECT COUNT(*) FROM orders WHERE payment_status = 'paid' AND (payment_confirmed IS NULL OR payment_confirmed = 0)").fetchone()[0]
                counts['admin_pending_payments'] = pending_payments
                
                # Count Pending Listings Modifications
                pending_modifications = conn.execute("SELECT COUNT(*) FROM listings WHERE status = 'modification_pending'").fetchone()[0]
                counts['admin_pending_modifications'] = pending_modifications

                # Count Pending Refunds & Supplements
                # 1. Refund Tasks
                pending_refund_tasks = conn.execute("SELECT COUNT(*) FROM refund_tasks WHERE status = 'pending'").fetchone()[0]
                
                # 2. Underpaid orders (buyer still owes a supplement)
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
                
            if role == Role.MEMBER:
                # Count all active orders for member (pending + confirmed + in_progress)
                # This matches the total badges shown in the order tabs
                pending_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE buyer_id = ? AND status = 'pending'", (user_id,)).fetchone()[0]
                confirmed_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE buyer_id = ? AND status = 'confirmed'", (user_id,)).fetchone()[0]
                in_progress_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE buyer_id = ? AND status = 'in_progress'", (user_id,)).fetchone()[0]
                
                counts['member_pending_orders'] = pending_orders
                counts['member_upcoming_orders'] = confirmed_orders + in_progress_orders
                counts['member_badge_count'] = pending_orders + confirmed_orders + in_progress_orders

        except Exception:
            current_app.logger.exception('Error injecting sidebar counts')

    return dict(sidebar_counts=counts)
