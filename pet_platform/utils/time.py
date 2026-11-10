"""Time helpers.

Stored timestamps are written by SQLite as server-local time (NOW_SQL). That is kept
as-is on purpose: changing it would shift every existing row.
"""
from datetime import datetime, timedelta, timezone

TZ_UTC8 = timezone(timedelta(hours=8))  # Taipei time

# SQL expression used for created_at / updated_at columns.
NOW_SQL = "datetime('now', 'localtime')"


def now_utc8():
    """Current time in UTC+8."""
    return datetime.now(TZ_UTC8)


def utc8_filter(dt_str):
    """Jinja filter `utc8`: show a timestamp in UTC+8; naive values are treated as UTC."""
    if not dt_str:
        return ''
    try:
        if isinstance(dt_str, str):
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        else:
            dt = dt_str

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(TZ_UTC8).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        # Unparseable values are shown unchanged
        return str(dt_str)
