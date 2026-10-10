from slowapi import Limiter
from slowapi.util import get_remote_address

# Shared limiter instance — imported by all routers that need per-route limits.
# The global default (60/minute) is applied via SlowAPIMiddleware in main.py.
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
