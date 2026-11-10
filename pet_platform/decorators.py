"""Access-control decorators. Responses match the inline checks they replaced."""
from functools import wraps

from flask import redirect, session, url_for


def login_required(view=None, *, json=False):
    """Not logged in -> redirect to login page, or JSON 401 for APIs (json=True)."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if 'user_id' not in session:
                if json:
                    return {'error': 'Unauthorized'}, 401
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return wrapped

    if view is not None:
        return decorator(view)
    return decorator


def role_required(*roles, json=False, status=403, redirect_to='index'):
    """Wrong role (or not logged in) -> redirect to `redirect_to`, or JSON error with `status`.

    The role is only ever set at login together with user_id, so checking both is
    equivalent to the old `session.get('user_role') != ...` checks.
    """
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if 'user_id' not in session or session.get('user_role') not in roles:
                if json:
                    return {'error': 'Unauthorized'}, status
                return redirect(url_for(redirect_to))
            return f(*args, **kwargs)
        return wrapped
    return decorator
