#!/usr/bin/env python3
# ============================================================
#  tracker.py  —  Time-series tracker
#  Import شده توسط monitor.py — در همان process اجرا می‌شود
# ============================================================

import time
import logging
from collections import deque
from threading import Lock
from datetime import datetime
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


# ============================================================
#  Network Rate Tracker
# ============================================================

class NetworkTracker:
    """میانگین MB/min برای RX و TX هر VPS در پنجره ۱۰ دقیقه."""
    WINDOW_SEC = 600

    def __init__(self):
        self._lock    = Lock()
        self._samples: Dict[str, deque] = {}

    def add_sample(self, vps_name: str, rx_gb: float, tx_gb: float) -> None:
        with self._lock:
            buf = self._samples.setdefault(vps_name, deque())
            now = time.time()
            if buf and rx_gb < buf[-1][1] * 0.5:
                log.info(f"[{vps_name}] network counter reset — clearing")
                buf.clear()
            buf.append((now, rx_gb, tx_gb))
            cutoff = now - self.WINDOW_SEC
            while buf and buf[0][0] < cutoff:
                buf.popleft()

    def get_rate(self, vps_name: str) -> dict:
        with self._lock:
            samples = list(self._samples.get(vps_name, []))
        if len(samples) < 2:
            return {"rx_mb_per_min": 0.0, "tx_mb_per_min": 0.0,
                    "window_min": 0.0, "samples": len(samples)}
        ts0, rx0, tx0 = samples[0]
        ts1, rx1, tx1 = samples[-1]
        elapsed_min = (ts1 - ts0) / 60.0
        if elapsed_min < 0.05:
            return {"rx_mb_per_min": 0.0, "tx_mb_per_min": 0.0,
                    "window_min": 0.0, "samples": len(samples)}
        return {
            "rx_mb_per_min": round(max(0.0, (rx1 - rx0) * 1024) / elapsed_min, 2),
            "tx_mb_per_min": round(max(0.0, (tx1 - tx0) * 1024) / elapsed_min, 2),
            "window_min":    round(elapsed_min, 1),
            "samples":       len(samples),
        }


# ============================================================
#  Marzban User Tracker
# ============================================================

class MarzbanTracker:
    """تحلیل داده‌های کاربران Marzban."""

    def __init__(self):
        self._lock        = Lock()
        self._users:  List[dict] = []
        self._updated_at: float  = 0.0

    def update(self, users: List[dict]) -> None:
        with self._lock:
            self._users      = users
            self._updated_at = time.time()

    @property
    def is_fresh(self) -> bool:
        return bool(self._users) and (time.time() - self._updated_at) < 600

    def _snapshot(self) -> List[dict]:
        with self._lock:
            return list(self._users)

    def summary(self) -> dict:
        users = self._snapshot()
        now   = time.time()
        online   = sum(1 for u in users if self._is_online(u, now, 180))
        active10 = sum(1 for u in users if self._is_online(u, now, 600))
        expired  = sum(1 for u in users if u.get("expire") and u["expire"] < now)
        return {"total": len(users), "online_now": online,
                "active_10min": active10, "expired": expired,
                "updated_at": self._updated_at}

    def active_last_n_min(self, minutes: int = 10) -> List[str]:
        cutoff = time.time() - minutes * 60
        return [u.get("username", "?") for u in self._snapshot()
                if u.get("online_at") and (u["online_at"] or 0) > cutoff]

    def new_users_count(self) -> dict:
        now = time.time()
        counts = {"day": 0, "week": 0, "month": 0}
        for u in self._snapshot():
            ts = self._parse_ts(u.get("created_at"))
            if not ts: continue
            age = now - ts
            if age < 86_400:    counts["day"]   += 1
            if age < 604_800:   counts["week"]  += 1
            if age < 2_592_000: counts["month"] += 1
        return counts

    def top_users(self, n: int = 5) -> List[dict]:
        now = time.time()
        sorted_u = sorted(self._snapshot(),
                          key=lambda u: u.get("used_traffic") or 0, reverse=True)
        result = []
        for u in sorted_u[:n]:
            used  = u.get("used_traffic") or 0
            limit = u.get("data_limit")   or 0
            result.append({
                "username": u.get("username", "?"),
                "used_gb":  round(used  / 1_073_741_824, 2),
                "limit_gb": round(limit / 1_073_741_824, 2) if limit else None,
                "percent":  round(used / limit * 100, 1)    if limit else None,
                "online":   self._is_online(u, now, 180),
                "status":   u.get("status", "?"),
            })
        return result

    def expiring_soon(self) -> dict:
        now = time.time()
        in_24h, in_7d = [], []
        for u in self._snapshot():
            exp = u.get("expire")
            if not exp or exp <= now: continue
            name = u.get("username", "?")
            if exp < now + 86_400:
                in_24h.append({"username": name, "remaining": f"{int((exp-now)/3600)}h"})
            elif exp < now + 604_800:
                in_7d.append({"username": name, "remaining": f"{int((exp-now)/86400)}d"})
        return {"in_24h": in_24h, "in_7d": in_7d}

    def sold_traffic(self) -> dict:
        """مجموع data_limit کاربران بر اساس تاریخ ساخت."""
        now    = time.time()
        totals = {"all": 0, "month": 0, "week": 0, "day": 0}
        for u in self._snapshot():
            limit = u.get("data_limit") or 0
            if not limit: continue
            totals["all"] += limit
            ts = self._parse_ts(u.get("created_at"))
            if ts:
                age = now - ts
                if age < 86_400:    totals["day"]   += limit
                if age < 604_800:   totals["week"]  += limit
                if age < 2_592_000: totals["month"] += limit
        return {k: round(v / 1_073_741_824, 2) for k, v in totals.items()}

    @staticmethod
    def _is_online(u: dict, now: float, threshold_sec: int) -> bool:
        oa = u.get("online_at")
        return bool(oa and (now - oa) < threshold_sec)

    @staticmethod
    def _parse_ts(value) -> Optional[float]:
        if not value: return None
        if isinstance(value, (int, float)): return float(value)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(
                    value.replace("Z", "+00:00")).timestamp()
            except Exception: pass
        return None
