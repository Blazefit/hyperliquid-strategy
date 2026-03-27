"""
Gunicorn config for Exp108 dashboard.
Prevents port exhaustion by:
  - Single worker (low-traffic dashboard)
  - Short keep-alive (free connections quickly)
  - Worker recycling (prevent long-lived connection/memory leaks)
  - Graceful timeout (clean shutdown)
"""

import os

bind = f"0.0.0.0:{os.environ.get('HL_DASH_PORT', '8181')}"
workers = 1
threads = 2
worker_class = "gthread"

# Connection management — the core port exhaustion fix
keepalive = 5          # close idle connections after 5s (default 2)
timeout = 30           # kill worker if request takes >30s
graceful_timeout = 10  # 10s for graceful shutdown

# Worker recycling — prevents slow leaks from accumulating
max_requests = 1000          # restart worker after 1000 requests
max_requests_jitter = 100    # randomize to avoid thundering herd

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Process naming
proc_name = "exp108-dashboard"

# Preload app to share memory across threads
preload_app = True
