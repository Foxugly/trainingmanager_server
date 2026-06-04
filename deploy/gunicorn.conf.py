# Gunicorn config for the Training Manager API (prod).
# Bound to a local TCP port; nginx reverse-proxies to it (127.0.0.1:8005).
# Port 8005 is this site's unique slot in the fleet (OPERATIONS.md §3.4).

bind = "127.0.0.1:8005"
workers = 3
worker_class = "sync"
timeout = 120          # AI endpoints call Claude synchronously; allow headroom.
graceful_timeout = 30
keepalive = 5

accesslog = "/var/log/tm/gunicorn-access.log"
errorlog = "/var/log/tm/gunicorn-error.log"
loglevel = "info"

preload_app = True
