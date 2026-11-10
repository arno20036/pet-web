"""Order APIs: cancel, reschedule, admin refund tasks."""
from flask import current_app, request, session

from pet_platform.constants import Role
from pet_platform.db import get_db
from pet_platform.decorators import login_required, role_required
from pet_platform.services import order_service, payment_service, ServiceError

from pet_platform.blueprints.api import bp


@bp.route('/api/order/<int:order_id>/reschedule', methods=['POST'])
@login_required(json=True)
def api_reschedule_order(order_id):
    data = request.get_json()
    new_date = data.get('date')
    new_time = data.get('time')
    new_duration = int(data.get('duration', 1))
    
    if not all([new_date, new_time, new_duration]):
        return {'error': 'Missing fields'}, 400

    try:
        order_service.request_reschedule(order_id, session['user_id'], new_date, new_time, new_duration)
    except ServiceError as e:
        return {'error': e.message}, e.status
    except Exception:
        current_app.logger.exception('Reschedule error')
        return {'error': 'Database error'}, 500
    return {'success': True}

@bp.route('/api/order/<int:order_id>/reschedule/respond', methods=['POST'])
@role_required(Role.TRAINER, json=True, status=401)
def api_respond_reschedule(order_id):
    action = request.get_json().get('action')  # 'approve' or 'reject'
    if action not in ['approve', 'reject']:
        return {'error': 'Invalid action'}, 400

    try:
        msg = order_service.respond_reschedule(order_id, session['user_id'], action)
    except ServiceError as e:
        return {'error': e.message}, e.status
    except Exception as e:
        current_app.logger.exception('Reschedule response error')
        get_db().rollback()
        return {'error': f'操作失敗: {str(e)}'}, 500
    return {'success': True, 'message': msg}

@bp.route('/api/order/<int:order_id>/cancel', methods=['POST'])
@login_required(json=True)
def api_cancel_order(order_id):
    try:
        order_service.cancel(order_id, session['user_id'], request.get_json() or {})
    except ServiceError as e:
        return {'error': e.message}, e.status
    except Exception as e:
        get_db().rollback()
        current_app.logger.exception('Cancel error')
        return {'error': f'System Error: {str(e)}'}, 500
    return {'success': True}

# Admin Refund APIs
@bp.route('/api/admin/refund-task/<int:order_id>', methods=['GET'])
@role_required(Role.ADMIN, json=True)
def get_admin_refund_task(order_id):
    conn = get_db()
    task = conn.execute('SELECT * FROM refund_tasks WHERE order_id = ? ORDER BY created_at DESC LIMIT 1', (order_id,)).fetchone()
    
    if not task:
        return {'error': 'Refund task not found'}, 404
        
    return {'task': dict(task)}

@bp.route('/api/admin/refund-task/<int:task_id>/process', methods=['POST'])
@role_required(Role.ADMIN, json=True)
def process_admin_refund_task(task_id):
    task = payment_service.get_refund_task(task_id)
    if not task:
        return {'error': 'Task not found'}, 404
    try:
        payment_service.process_refund_task(task, session['user_id'], source='API')
    except Exception as e:
        return {'error': str(e)}, 500
    return {'success': True}
