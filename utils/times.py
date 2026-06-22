from datetime import datetime, timezone


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
