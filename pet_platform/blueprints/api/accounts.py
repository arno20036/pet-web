"""Saved payment methods, bank accounts and blacklist APIs."""
import sqlite3

from flask import current_app, request, session

from pet_platform.constants import Role
from pet_platform.db import get_db
from pet_platform.decorators import login_required, role_required
from pet_platform.services import bank_account_service

from pet_platform.blueprints.api import bp


@bp.route('/api/payment_methods/<int:method_id>', methods=['DELETE'])
@login_required(json=True)
def delete_payment_method(method_id):
    conn = get_db()
    conn.execute('DELETE FROM saved_payment_methods WHERE id = ? AND user_id = ?', (method_id, session['user_id']))
    conn.commit()
    
    return {'success': True}

# Bank accounts: the old URLs stay, all backed by bank_account_service.
@bp.route('/api/bank_accounts/<int:account_id>', methods=['DELETE'])
@login_required(json=True)
def delete_bank_account(account_id):
    bank_account_service.delete_account('trainer', account_id, session['user_id'])
    return {'success': True}

@bp.route('/api/user/bank-accounts', methods=['GET'])
@login_required(json=True)
def get_user_bank_accounts():
    return {'accounts': bank_account_service.list_accounts('member', session['user_id'], default_first=True)}

@bp.route('/api/user/bank-accounts', methods=['POST'])
@login_required(json=True)
def add_user_bank_account():
    data = request.get_json()
    if not all([data.get('bank_code'), data.get('account_number'), data.get('account_name')]):
        return {'error': 'Missing fields'}, 400
    bank_account_service.add_member_account(session['user_id'], data['bank_code'], data['account_number'],
                                            data['account_name'], is_default=data.get('is_default', False))
    return {'success': True}

@bp.route('/api/user/bank-accounts/<int:account_id>', methods=['DELETE'])
@login_required(json=True)
def api_delete_user_bank_account(account_id):
    bank_account_service.delete_account('member', account_id, session['user_id'])
    return {'success': True}

@bp.route('/api/blacklist/add', methods=['POST'])
@role_required(Role.TRAINER, json=True, status=401)
def add_to_blacklist():
    data = request.get_json()
    consumer_id = data.get('consumer_id')
    
    if not consumer_id:
        return {'error': 'Missing consumer_id'}, 400
        
    conn = get_db()
    try:
        conn.execute('INSERT INTO blacklists (trainer_id, consumer_id) VALUES (?, ?)', (session['user_id'], consumer_id))
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False # Already blacklisted
    except Exception as e:
        current_app.logger.exception('Error adding to blacklist')
        return {'error': str(e)}, 500
    
    return {'success': success}

@bp.route('/api/blacklist/remove', methods=['POST'])
@role_required(Role.TRAINER, json=True, status=401)
def remove_from_blacklist():
    data = request.get_json()
    consumer_id = data.get('consumer_id')
    
    if not consumer_id:
        return {'error': 'Missing consumer_id'}, 400
        
    conn = get_db()
    conn.execute('DELETE FROM blacklists WHERE trainer_id = ? AND consumer_id = ?', (session['user_id'], consumer_id))
    conn.commit()
    return {'success': True}
