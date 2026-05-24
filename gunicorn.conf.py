# Gunicorn configuration for all_sanctions
# ─────────────────────────────────────────
# Single sync worker: L1 (_entity_cache) stays warm across requests with no
# inter-process memory duplication. preload_app=False keeps the master process
# lightweight — no entity data loaded until a real request arrives.

# 1 process with 4 threads — handles concurrent requests without duplicating
# large in-memory entity caches across processes (which caused OOM).
# gthread is built into Gunicorn, no extra packages needed.
workers = 1

worker_class = "gthread"
threads = 4

# Match Fly's edge proxy timeout. Cold-fetch endpoints that risk exceeding
# this window already use the warm-cache contracts in routes/datasets.py
# (_warm_medicaid_names, _medicaid_entities) to return [] rather than block.
timeout = 60

graceful_timeout = 30

keepalive = 5

import os
bind = f"0.0.0.0:{os.environ.get('PORT', '5001')}"

accesslog = "-"
errorlog  = "-"
loglevel  = "info"

# Do NOT preload — let the single worker own its own memory space cleanly.
# preload_app=True caused OOM: master loaded all entity records, then forked
# workers copied pages via CoW, tripling memory usage on restart.
preload_app = False
