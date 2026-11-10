"""Shared pytest fixtures.

Every test runs against a *copy* of database.db in tmp_path, never the original.
"""
import shutil
import sqlite3
from pathlib import Path

import pytest

from pet_platform import create_app

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DB = PROJECT_ROOT / 'database.db'

PASSWORD = '123123'
ACCOUNTS = {
    'member': 'c001@gmail.com',
    'trainer': 't001@gmail.com',
    'admin': 'a001@gmail.com',
}


@pytest.fixture
def db_path(tmp_path):
    dst = tmp_path / 'database.db'
    shutil.copyfile(SOURCE_DB, dst)
    return dst


@pytest.fixture
def app(db_path):
    return create_app({'TESTING': True, 'DATABASE': str(db_path)})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def login_email(client, email):
    """Log in with any seeded account (README: every account's password is 123123)."""
    resp = client.post('/login', data={'email': email, 'password': PASSWORD})
    assert resp.status_code == 302, f'login as {email} failed: {resp.status_code}'
    with client.session_transaction() as sess:
        assert 'user_id' in sess, f'login as {email} did not set a session'
        return sess['user_id']


def login(client, role):
    resp = client.post('/login', data={'email': ACCOUNTS[role], 'password': PASSWORD})
    assert resp.status_code == 302, f'login as {role} failed: {resp.status_code}'
    with client.session_transaction() as sess:
        assert sess.get('user_role') == role, f'expected role {role}, got {sess.get("user_role")}'
        return sess['user_id']
