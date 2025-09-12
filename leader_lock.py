import os, time, signal, sys, logging
from upstash_redis import Redis

log = logging.getLogger(__name__)
r = Redis(url=os.environ["UPSTASH_REDIS_REST_URL"],
          token=os.environ["UPSTASH_REDIS_REST_TOKEN"])

LOCK_KEY    = os.getenv("LEADER_LOCK_KEY", "apnewsbot:leader")
LOCK_TTL    = int(os.getenv("LEADER_LOCK_TTL", "45"))
RENEW_EVERY = int(os.getenv("LEADER_LOCK_RENEW", "15"))
_running = True

def _stop(*_):
    global _running
    _running = False
    pid = str(os.getpid())
    try:
        if r.get(LOCK_KEY) == pid:
            r.delete(LOCK_KEY)
    finally:
        sys.exit(0)

for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, _stop)

def run_with_lock(loop_once):
    """Call loop_once repeatedly, but only while we hold the lock."""
    pid = str(os.getpid())
    while _running:
        if r.set(LOCK_KEY, pid, nx=True, ex=LOCK_TTL):
            log.info(f"Acquired leader lock {LOCK_KEY}")
            last = 0.0
            while _running:
                now = time.time()
                if now - last >= RENEW_EVERY:
                    if r.get(LOCK_KEY) == pid:
                        r.expire(LOCK_KEY, LOCK_TTL)
                    else:
                        log.info("Lost leader lock; re-acquiring…")
                        break
                    last = now
                loop_once()
        else:
            log.info(f"Waiting for leader lock {LOCK_KEY} …")
            time.sleep(2)
