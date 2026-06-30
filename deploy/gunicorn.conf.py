# Gunicorn config for the Training Manager API (prod).
# Bound to a local TCP port; nginx reverse-proxies to it (127.0.0.1:8005).
# Port 8005 is this site's unique slot in the fleet (OPERATIONS.md §3.4).

bind = "127.0.0.1:8005"
workers = 3
# Threaded workers: the AI endpoints call Claude synchronously (up to
# ANTHROPIC_TIMEOUT_SECONDS, currently 60s). With sync workers a few concurrent
# generations would occupy all 3 workers and stall the whole API. Those calls are
# I/O-bound (the GIL is released while waiting on the socket), so threads keep
# serving other requests meanwhile. 3 workers x 6 threads = 18 concurrent slots.
worker_class = "gthread"
threads = 6
timeout = 120          # > the 60s AI ceiling, with headroom.
graceful_timeout = 30
keepalive = 5

accesslog = "/var/log/tm/gunicorn-access.log"
errorlog = "/var/log/tm/gunicorn-error.log"
loglevel = "info"

preload_app = True
