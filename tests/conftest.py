"""Select a disposable database before importing the API and isolate test history."""

import os
import tempfile

# Always isolate tests, even when the developer configured a production database.
_TEST_DATABASE = os.path.join(tempfile.mkdtemp(prefix="ribbon_tests_"), "history.db")
os.environ["RIBBON_DB"] = os.environ["PYCONFER_DB"] = _TEST_DATABASE

import pytest  # noqa: E402 (configure the test database before importing)

from core import storage  # noqa: E402


@pytest.fixture(autouse=True)
def clean_history():
    """Clear temporary history between tests without deleting an open SQLite file."""
    storage.create_schema()
    with storage._connection() as connection:
        for table in ("page", "missing_slip", "audit", "session", "app_user"):
            connection.execute(f"DELETE FROM {table}")
    yield
