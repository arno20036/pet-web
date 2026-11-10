"""Tests for the two logic fixes in refactor step 3.

1. Trainer order actions must respect the previous status (transition table).
2. Completing an order pays the trainer and reports the commission rate
   (it used to call a function that does not exist, so the message never appeared).
"""
import pytest

from conftest import login


def _trainer_id(db):
    return db.execute("SELECT id FROM users WHERE email = 't001@gmail.com'").fetchone()[0]


def _make_order(db, status, payment_status='unpaid', payment_confirmed=0):
    """Turn one of t001's orders into the requested state; returns its id."""
    trainer = _trainer_id(db)
    order_id = db.execute('SELECT id FROM orders WHERE seller_id = ? ORDER BY id LIMIT 1', (trainer,)).fetchone()[0]
    db.execute('UPDATE orders SET status = ?, payment_status = ?, payment_confirmed = ?, booking_date = ? WHERE id = ?',
               (status, payment_status, payment_confirmed, '2099-01-01', order_id))
    db.execute("DELETE FROM account_transactions WHERE order_id = ? AND type = 'income'", (order_id,))
    db.commit()
    return order_id


def _flashes(client):
    with client.session_transaction() as s:
        return [msg for _, msg in s.get('_flashes', [])]


def _status(db, order_id):
    return db.execute('SELECT status FROM orders WHERE id = ?', (order_id,)).fetchone()[0]


def _income_rows(db, order_id):
    return db.execute("SELECT COUNT(*) FROM account_transactions WHERE order_id = ? AND type = 'income'",
                      (order_id,)).fetchone()[0]


@pytest.mark.parametrize('status,action', [
    ('pending', 'complete'),      # skipping straight to complete
    ('confirmed', 'complete'),    # not started yet
    ('completed', 'accept'),      # accepting a finished order
    ('cancelled', 'start'),
    ('in_progress', 'reject'),
])
def test_action_rejected_from_wrong_status(client, db, status, action):
    order_id = _make_order(db, status, 'paid', 1)
    login(client, 'trainer')
    client.post(f'/trainer/order/{order_id}/{action}')

    assert _status(db, order_id) == status
    assert _income_rows(db, order_id) == 0
    assert '訂單目前的狀態無法執行此操作' in _flashes(client)


def test_unpaid_order_cannot_start(client, db):
    order_id = _make_order(db, 'confirmed', 'unpaid', 0)
    login(client, 'trainer')
    client.post(f'/trainer/order/{order_id}/start')
    assert _status(db, order_id) == 'confirmed'


def test_paid_but_unconfirmed_order_cannot_start(client, db):
    order_id = _make_order(db, 'confirmed', 'paid', 0)
    login(client, 'trainer')
    client.post(f'/trainer/order/{order_id}/start')
    assert _status(db, order_id) == 'confirmed'
    assert any('尚未確認收款' in m for m in _flashes(client))


def test_happy_path_accept_start_complete(client, db):
    order_id = _make_order(db, 'pending', 'paid', 1)
    login(client, 'trainer')
    for action, expected in (('accept', 'confirmed'), ('start', 'in_progress'), ('complete', 'completed')):
        client.post(f'/trainer/order/{order_id}/{action}?force=1')
        assert _status(db, order_id) == expected


def test_complete_pays_trainer_and_reports_commission(client, db):
    order_id = _make_order(db, 'in_progress', 'paid', 1)
    platform_rows_before = db.execute(
        "SELECT COUNT(*) FROM platform_transactions WHERE related_order_id = ? AND type = 'payout_allocation'",
        (order_id,)).fetchone()[0]

    login(client, 'trainer')
    client.post(f'/trainer/order/{order_id}/complete')

    assert _status(db, order_id) == 'completed'
    assert _income_rows(db, order_id) == 1
    platform_rows_after = db.execute(
        "SELECT COUNT(*) FROM platform_transactions WHERE related_order_id = ? AND type = 'payout_allocation'",
        (order_id,)).fetchone()[0]
    assert platform_rows_after == platform_rows_before + 1
    assert any('款項已發放至您的帳戶' in m and '% 平台費用' in m for m in _flashes(client))
