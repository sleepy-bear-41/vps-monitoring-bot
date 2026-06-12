# ============================================================
#  monitor_config.example.py  —  VPS خارج (پنل اصلی)
#  کپی کن به monitor_config.py و مقادیر واقعی را وارد کن
# ============================================================

# ─── Agent نود ایران ─────────────────────────────────────────
IRAN_NODE_API = {
    "metrics_url": "https://YOUR_IRAN_DOMAIN/metrics",
    "health_url":  "https://YOUR_IRAN_DOMAIN/health",
    "api_key":     "SAME_KEY_AS_NODE_CONFIG",
    "timeout":     15,
}

# ─── Marzban روی VPS خارج (localhost) ────────────────────────
MARZBAN = {
    "url":  "http://localhost:7777",
    "user": "admin",
    "pass": "YOUR_MARZBAN_PASSWORD",
}

# ─── ربات تلگرام اصلی (@MainBot) ─────────────────────────────
TELEGRAM = {
    "token":     "MAIN_BOT_TOKEN",
    "chat_id":   "YOUR_CHAT_ID",
    "admin_ids": [],
}

# ─── آستانه هشدارها ──────────────────────────────────────────
THRESHOLDS = {
    "cpu_percent":   85,
    "ram_percent":   85,
    "disk_percent":  85,
    "swap_percent":  70,
    "load_avg_1":    4.0,
    "packet_loss":   5,
    "ping_ms":       300,
    "cpu_temp":      80,
    "inode_percent": 85,
}

# ─── فواصل زمانی (ثانیه) ─────────────────────────────────────
INTERVALS = {
    "poll_iran_node":  60,
    "poll_local":      60,
    "fetch_users":     300,
    "log_check":       300,
    "periodic_report": 3600,
    "heartbeat":       180,
}

# ─── سرویس‌های حیاتی VPS خارج ───────────────────────────────
LOCAL_SERVICES = ["docker", "marzban", "nginx", "xray"]

LOG_LINES = 50
