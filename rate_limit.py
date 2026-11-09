import time, threading

class TokenBucket:
    def __init__(self, rate_per_min, burst):
        self.rate = rate_per_min / 60.0
        self.burst = burst
        self.tokens = burst
        self.last = time.time()
        self.lock = threading.Lock()

    def consume(self, n=1):
        with self.lock:
            now = time.time()
            elapsed = now - self.last
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last = now
            if self.tokens >= n:
                self.tokens -= n
                return True, 0
            # time until enough
            need = n - self.tokens
            wait = need / self.rate if self.rate > 0 else float('inf')
            return False, wait

_buckets = {}
_lock = threading.Lock()

def get_bucket(key_id):
    import db
    rpm = int(db.get_setting("rate_limit_rpm", "60"))
    burst = int(db.get_setting("rate_limit_burst", "10"))
    with _lock:
        b = _buckets.get(key_id)
        if b is None or b.rate != rpm/60.0 or b.burst != burst:
            b = TokenBucket(rpm, burst)
            _buckets[key_id] = b
        return b

def check(key_id):
    import db
    if db.get_setting("rate_limit_enabled", "1") != "1":
        return True, 0
    b = get_bucket(key_id)
    return b.consume(1)
