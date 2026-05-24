#!/usr/bin/env python3
"""
OpenSanctions Web UI — application factory.
"""

import sys
import os
import threading
import logging

# Allow imports from project root when running from any directory
sys.path.insert(0, os.path.dirname(__file__))

# Configure logging once at startup so INFO-level messages (cache warmup,
# init banners) reach stdout under both Werkzeug and Gunicorn.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
)

from flask import Flask, jsonify, render_template

logger = logging.getLogger(__name__)
from routes.datasets import datasets_bp
from routes.cyber import cyber_bp
from routes.entity_search import entity_search_bp
from routes.resources import resources_bp
from routes.census import census_bp
import cache as l2
import db
from settings import SECRET_KEY

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = SECRET_KEY

app.register_blueprint(datasets_bp)
app.register_blueprint(cyber_bp)
app.register_blueprint(entity_search_bp)
app.register_blueprint(resources_bp)
app.register_blueprint(census_bp)

# Initialise L2 cache + persistence tables on startup
# (l2.init is a no-op if a test fixture already injected a client)
with app.app_context():
    l2.init()
    db.init()


def _background_warmup():
    """
    Promote already-cached datasets from L2 (Redis) → L1 (in-memory) at
    startup.  This is a read-only operation — it NEVER makes network requests.

    Network fetches only happen when a user actually requests data that isn't
    in either cache layer.  Keeping warmup network-free prevents the worker
    timeout cascade that occurs when sync workers block on simultaneous L3
    origin fetches during startup.
    """
    import time
    from data import _entity_cache
    import cache as _l2

    time.sleep(2)  # let Gunicorn finish binding

    logger.info("Cache warmup: promoting L2 → L1 (no network)")
    try:
        promoted = 0
        for key in _l2._client.scan_iter(match="entity:*", count=500):
            key_str = key.decode() if isinstance(key, bytes) else key
            ds_name = key_str.split(":", 1)[1]
            # Skip param-variant keys (entity:<name>:<hash>) — only warm bare datasets
            if ":" in ds_name:
                continue
            if ds_name in _entity_cache:
                continue  # already in L1
            data = _l2.get("entity", ds_name)
            if data is not None:
                _entity_cache[ds_name] = data
                promoted += 1

        logger.info("Cache warmup: promoted %d datasets from L2 → L1", promoted)
    except Exception as exc:
        logger.warning("Cache warmup: aborted — %s", exc)


_warmup_thread = threading.Thread(target=_background_warmup, daemon=True)
_warmup_thread.start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    """
    Lightweight liveness probe for Fly's HTTP health check. Avoids the
    templating layer entirely so it stays fast even if a slow upstream
    response is in flight.
    """
    return "ok", 200


@app.route("/api/cache-stats")
def api_cache_stats():
    """Return L2 cache stats per source — entries, hit rate, avg age."""
    return jsonify(l2.stats())


@app.route("/api/cache-invalidate", methods=["POST"])
def api_cache_invalidate():
    """
    Manually invalidate cache entries.
    Body: {"source": "entity", "identifier": "us_ofac_sdn"}  — one dataset
    Body: {"source": "entity"}                                — all entity rows
    Body: {}                                                  — entire cache
    """
    from flask import request
    body       = request.get_json(force=True) or {}
    source     = body.get("source")
    identifier = body.get("identifier")
    l2.invalidate(source=source, identifier=identifier)
    return jsonify({"ok": True, "source": source, "identifier": identifier})


if __name__ == "__main__":
    print("OpenSanctions Explorer → http://localhost:5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
