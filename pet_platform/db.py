"""Per-request SQLite connection stored on flask.g, closed automatically at teardown."""
import os
import sqlite3

import click
from flask import current_app, g
from flask.cli import with_appcontext

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'schema.sql')


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(exc=None):
    # Uncommitted changes are discarded here, exactly like the old manual conn.close().
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """Create any missing tables from schema.sql. Never drops or changes existing data."""
    with open(SCHEMA_PATH, encoding='utf-8') as f:
        get_db().executescript(f.read())
    get_db().commit()


@click.command('init-db')
@with_appcontext
def init_db_command():
    """flask --app app init-db"""
    init_db()
    click.echo(f"Database ready: {current_app.config['DATABASE']}")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
