"""Template context shared by every page (sidebar badge counts)."""
from flask import current_app, session

from pet_platform.services.stats_service import sidebar_counts


def inject_sidebar_counts():
    counts = {}
    if 'user_id' in session:
        try:
            counts = sidebar_counts(session['user_id'], session.get('user_role'))
        except Exception:
            current_app.logger.exception('Error injecting sidebar counts')
    return dict(sidebar_counts=counts)
