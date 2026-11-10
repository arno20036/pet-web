"""Smoke tests: every GET page each role can reach must not return 500.

Routes needing an id get one from the (copied) database at test time.
If no suitable row exists, the case is skipped rather than failed.
"""
import re
from pathlib import Path

import pytest
from flask import template_rendered

from conftest import ACCOUNTS, PROJECT_ROOT, login, login_email

PAGE_OK = {200, 302}
# JSON APIs may legitimately answer 4xx (e.g. 404 when no refund task exists).
API_OK = {200, 302, 400, 401, 403, 404}


# ---------------------------------------------------------------------------
# id lookups, evaluated against the database copy
# ---------------------------------------------------------------------------

def _scalar(db, sql, params=()):
    row = db.execute(sql, params).fetchone()
    return None if row is None else row[0]


def _user_id(db, role):
    return _scalar(db, 'SELECT id FROM users WHERE email = ?', (ACCOUNTS[role],))


def _upload_file():
    uploads = PROJECT_ROOT / 'uploads'
    files = sorted(p.name for p in uploads.iterdir() if p.is_file()) if uploads.is_dir() else []
    ascii_files = [f for f in files if f.isascii()]
    return (ascii_files or files or [None])[0]


def resolve_ids(db):
    member = _user_id(db, 'member')
    trainer = _user_id(db, 'trainer')
    ids = {
        'member_id': member,
        'trainer_id': trainer,
        'listing_id': _scalar(db, 'SELECT id FROM listings ORDER BY id LIMIT 1'),
        'own_listing_id': _scalar(db, 'SELECT id FROM listings WHERE trainer_id = ? ORDER BY id LIMIT 1', (trainer,)),
        'member_order_id': _scalar(db, 'SELECT id FROM orders WHERE buyer_id = ? ORDER BY id DESC LIMIT 1', (member,)),
        # an order c001 has not appealed yet, so the appeal form actually renders
        'appealable_order_id': _scalar(db, '''
            SELECT o.id FROM orders o
              JOIN listings l ON l.id = o.listing_id
              JOIN users u ON u.id = o.seller_id
            WHERE o.buyer_id = ?
              AND NOT EXISTS (SELECT 1 FROM order_appeals a WHERE a.order_id = o.id)
            ORDER BY o.id DESC LIMIT 1''', (member,)),
        'trainer_order_id': _scalar(db, 'SELECT id FROM orders WHERE seller_id = ? ORDER BY id DESC LIMIT 1', (trainer,)),
        'appeal_id': _scalar(db, 'SELECT id FROM order_appeals ORDER BY id LIMIT 1'),
        'application_id': _scalar(db, 'SELECT id FROM trainer_applications ORDER BY id LIMIT 1'),
        'refund_order_id': _scalar(db, 'SELECT order_id FROM refund_tasks ORDER BY id LIMIT 1'),
        'upload_file': _upload_file(),
    }
    return ids


# ---------------------------------------------------------------------------
# route tables: (url template, is_api)
# ---------------------------------------------------------------------------

PUBLIC_ROUTES = [
    ('/', False),
    ('/login', False),
    ('/register', False),
    ('/listing/{listing_id}', False),
    ('/trainer/{trainer_id}/listings', False),
    ('/uploads/{upload_file}', False),
]

MEMBER_ROUTES = [
    ('/member/profile', False),
    ('/member/orders', False),
    ('/member/orders?start_date=2020-01-01&end_date=2099-12-31', False),
    ('/member/order/{appealable_order_id}/appeal', False),
    ('/member/order/{member_order_id}/payment', False),
    ('/member/apply-trainer', False),
    ('/booking/{listing_id}', False),
    ('/api/user/bank-accounts', True),
    ('/api/order/{member_order_id}/messages', True),
    ('/api/chat/context?user_id={trainer_id}', True),
    ('/api/chat/unread_count', True),
    ('/api/chat/contacts', True),
    ('/api/users/{trainer_id}/reviews', True),
]

TRAINER_ROUTES = [
    ('/trainer/create-listing', False),
    ('/trainer/manage-listings', False),
    ('/trainer/listing/{own_listing_id}', False),
    ('/trainer/listing/{own_listing_id}/edit', False),
    ('/trainer/calendar', False),
    ('/trainer/orders', False),
    ('/trainer/level-history', False),
    ('/trainer/account', False),
    ('/trainer/withdrawal', False),
    ('/trainer/reviews', False),
    ('/trainer/blacklist', False),
    ('/member/profile', False),
    ('/api/order/{trainer_order_id}/messages', True),
    ('/api/chat/context?user_id={member_id}', True),
    ('/api/chat/unread_count', True),
    ('/api/chat/contacts', True),
    ('/api/users/{member_id}/reviews', True),
]

ADMIN_ROUTES = [
    ('/admin/listing-review', False),
    ('/admin/listing-modification-review', False),
    ('/admin/listing-review/{listing_id}', False),
    ('/admin/order-tracking', False),
    ('/admin/complaints', False),
    ('/admin/trainer-review', False),
    ('/admin/trainer-review/{application_id}', False),
    ('/admin/refunds', False),
    ('/admin/member-management', False),
    ('/admin/member-management/{member_id}', False),
    ('/admin/member-management/{member_id}/edit', False),
    ('/admin/statistics', False),
    ('/admin/appeals', False),
    ('/admin/appeal/{appeal_id}', False),
    ('/admin/platform-settings', False),
    ('/admin/withdrawal-requests', False),
    ('/api/admin/refund-task/{refund_order_id}', True),
]

ROLE_ROUTES = {
    'member': MEMBER_ROUTES,
    'trainer': TRAINER_ROUTES,
    'admin': ADMIN_ROUTES,
}

# Pages c001 can't reach with the seed data (no confirmed unpaid order, no appeal):
# log in as whichever seeded member owns a suitable row instead.
OWNER_CASES = {
    'payment': ('/member/order/{id}/payment', '''
        SELECT o.id, u.email FROM orders o JOIN users u ON u.id = o.buyer_id
        WHERE o.status = 'confirmed'
          AND (COALESCE(o.paid_amount, 0) < o.total_price OR o.payment_status != 'paid')
        ORDER BY o.id LIMIT 1'''),
    'appeal_detail': ('/member/appeal/{id}', '''
        SELECT a.id, u.email FROM order_appeals a JOIN users u ON u.id = a.user_id
        ORDER BY a.id LIMIT 1'''),
}

ALL_CASES = (
    [('anonymous', url, api) for url, api in PUBLIC_ROUTES]
    + [(role, url, api) for role in ('member', 'trainer', 'admin') for url, api in PUBLIC_ROUTES]
    + [(role, url, api) for role, routes in ROLE_ROUTES.items() for url, api in routes]
)


def _fill(url_template, ids):
    keys = re.findall(r'\{(\w+)\}', url_template)
    missing = [k for k in keys if ids.get(k) is None]
    if missing:
        return None, missing
    return url_template.format(**ids), []


def _get(client, role, url_template, ids):
    if role != 'anonymous':
        login(client, role)
    url, missing = _fill(url_template, ids)
    if url is None:
        pytest.skip(f'no data for {missing}')
    return url, client.get(url)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_login_each_role(client):
    for role in ('member', 'trainer', 'admin'):
        login(client, role)
        client.get('/logout')


@pytest.mark.parametrize('role,url_template,is_api', ALL_CASES,
                         ids=[f'{r}:{u}' for r, u, _ in ALL_CASES])
def test_get_pages(client, db, role, url_template, is_api):
    ids = resolve_ids(db)
    url, resp = _get(client, role, url_template, ids)
    allowed = API_OK if is_api else PAGE_OK
    assert resp.status_code in allowed, f'{role} GET {url} -> {resp.status_code}'


def _owner_case(db, key):
    url_template, sql = OWNER_CASES[key]
    row = db.execute(sql).fetchone()
    if row is None:
        return None, None
    return url_template.format(id=row[0]), row[1]


@pytest.mark.parametrize('key', list(OWNER_CASES))
def test_member_pages_as_owner(client, db, key):
    url, email = _owner_case(db, key)
    if url is None:
        pytest.skip(f'no data for {key}')
    login_email(client, email)
    resp = client.get(url)
    assert resp.status_code == 200, f'{email} GET {url} -> {resp.status_code}'


@pytest.mark.parametrize('url_template', [u for u, _ in MEMBER_ROUTES + TRAINER_ROUTES + ADMIN_ROUTES if '{' not in u and not u.startswith('/api/')])
def test_protected_pages_redirect_anonymous(client, url_template):
    resp = client.get(url_template)
    assert resp.status_code == 302, f'anonymous GET {url_template} -> {resp.status_code}'


# ---------------------------------------------------------------------------
# url_for coverage
# ---------------------------------------------------------------------------

URL_FOR_RE = re.compile(r"""url_for\(\s*['"]([^'"]+)['"]""")

# Pre-existing dead references (found in step 0, not fixed: out of the refactor's scope).
# admin_listing_review_detail.html uses them inside `{% if is_modification %}`, but the
# view never passes is_modification, so the branch never renders and never raises.
KNOWN_MISSING_ENDPOINTS = {
    'admin_listing_modification_approve',
    'admin_listing_modification_reject',
}


def _url_for_endpoints():
    sources = list((PROJECT_ROOT / 'templates').rglob('*.html'))
    sources.append(PROJECT_ROOT / 'app.py')
    pkg = PROJECT_ROOT / 'pet_platform'
    if pkg.is_dir():
        sources.extend(pkg.rglob('*.py'))
    found = {}
    for path in sources:
        for endpoint in URL_FOR_RE.findall(path.read_text(encoding='utf-8')):
            found.setdefault(endpoint, set()).add(path.relative_to(PROJECT_ROOT).as_posix())
    return found


def test_every_url_for_endpoint_exists(app):
    """Catches endpoints that were renamed (e.g. moved into a blueprint) but not updated."""
    known = set(app.view_functions)
    bad = {}
    for endpoint, files in _url_for_endpoints().items():
        if endpoint.startswith('.'):
            # blueprint-relative; resolved at request time, covered by test_get_pages
            continue
        if endpoint.split('.')[-1] in KNOWN_MISSING_ENDPOINTS:
            continue
        if endpoint not in known:
            bad[endpoint] = sorted(files)
    assert not bad, f'url_for() points at unknown endpoints: {bad}'


def test_render_all_pages_and_report_template_coverage(app, db):
    """Open every page as every role and render each template once.

    Any url_for BuildError inside a template surfaces here as a 500.
    Templates that no GET page renders are printed (run with -s to see them).
    """
    rendered = set()

    def record(sender, template, context, **extra):
        rendered.add(template.name)

    ids = resolve_ids(db)
    failures = []
    template_rendered.connect(record, app)
    try:
        for role, url_template, is_api in ALL_CASES:
            url, _ = _fill(url_template, ids)
            if url is None:
                continue
            client = app.test_client()
            if role != 'anonymous':
                login(client, role)
            status = client.get(url).status_code
            if status >= 500:
                failures.append(f'{role} GET {url} -> {status}')
        for key in OWNER_CASES:
            url, email = _owner_case(db, key)
            if url is None:
                continue
            client = app.test_client()
            login_email(client, email)
            status = client.get(url).status_code
            if status >= 500:
                failures.append(f'{email} GET {url} -> {status}')
    finally:
        template_rendered.disconnect(record, app)

    partials = {'base.html', 'member_layout.html', 'chat_snippet.html',
                'admin_generic.html', 'trainer_generic.html'}
    all_templates = {p.name for p in (PROJECT_ROOT / 'templates').glob('*.html')}
    not_rendered = sorted(all_templates - partials - rendered)
    print(f'\n[template coverage] rendered {len(rendered)} templates; '
          f'not reached by any GET: {not_rendered}')

    assert not failures, failures
