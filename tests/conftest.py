"""
Pytest fixtures.

`flask_app`    — session-scoped Flask app, backed by an in-memory fakeredis
                 instance so tests never touch a real Redis server.
`live_server`  — `flask_app` served over HTTP on an ephemeral port (Playwright).
`client`       — Flask test_client bound to `flask_app` (in-process, no server).
`base_url`     — URL of `live_server` (used by Playwright).
"""
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request

import fakeredis
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_up(url: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"Flask app did not come up at {url} within {timeout}s")


@pytest.fixture(scope="session")
def flask_app():
    """
    Import the Flask app with cache._client pointing at an in-memory fakeredis
    and DATABASE_URL pointing at a temp SQLite file. Both stores are torn down
    when the session ends.
    """
    import cache
    cache._client = fakeredis.FakeRedis(decode_responses=False)

    tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp_db.close()
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"

    import app as app_module
    yield app_module.app

    try:
        os.unlink(tmp_db.name)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _clean_db_tables(flask_app):
    """Truncate notes + address_history before each test for isolation."""
    import db
    from sqlalchemy import delete as sa_delete
    with db._engine.begin() as conn:
        conn.execute(sa_delete(db.notes))
        conn.execute(sa_delete(db.address_history))
    yield


@pytest.fixture(scope="session")
def live_server(flask_app):
    port = _free_port()
    server_thread = threading.Thread(
        target=flask_app.run,
        kwargs={
            "host": "127.0.0.1",
            "port": port,
            "debug": False,
            "use_reloader": False,
            "threaded": True,
        },
        daemon=True,
    )
    server_thread.start()
    url = f"http://127.0.0.1:{port}"
    _wait_until_up(url + "/")
    yield url


@pytest.fixture
def client(flask_app):
    """Flask test_client for in-process route tests."""
    return flask_app.test_client()


@pytest.fixture(scope="session")
def base_url(live_server):
    return live_server


@pytest.fixture(autouse=True)
def _stub_browser_apis(request):
    """
    Mock /api/* responses for Playwright tests so the SPA renders without
    real network. Skips tests that don't use the `page` fixture so cache
    and route tests don't pay the cost of spinning up a browser.
    """
    if "page" not in request.fixturenames:
        yield
        return
    page = request.getfixturevalue("page")
    page.route("**/api/**", lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body="[]",
    ))
    yield
