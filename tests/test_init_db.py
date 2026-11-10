"""`flask init-db` builds a complete database and never touches existing data."""
import sqlite3

from pet_platform import create_app

EXPECTED_TABLES = 23


def _tables(path):
    conn = sqlite3.connect(path)
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    counts = {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    conn.close()
    return counts


def test_init_db_creates_every_table_in_empty_database(tmp_path):
    path = tmp_path / 'new.db'
    app = create_app({'TESTING': True, 'DATABASE': str(path)})
    result = app.test_cli_runner().invoke(args=['init-db'])
    assert result.exit_code == 0, result.output

    counts = _tables(path)
    assert len(counts) == EXPECTED_TABLES
    assert counts['platform_settings'] == 1
    assert counts['platform_account'] == 1


def test_init_db_leaves_existing_data_alone(app, db_path):
    before = _tables(db_path)
    runner = app.test_cli_runner()
    assert runner.invoke(args=['init-db']).exit_code == 0
    assert runner.invoke(args=['init-db']).exit_code == 0
    assert _tables(db_path) == before
