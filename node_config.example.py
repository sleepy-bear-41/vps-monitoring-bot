# ============================================================
#  node_config.example.py  —  VPS ایران (نود)
#  کپی کن به node_config.py و مقادیر واقعی را وارد کن
# ============================================================

API_KEY    = "CHANGE_THIS_TO_A_RANDOM_32_CHAR_STRING"
AGENT_HOST = "127.0.0.1"
AGENT_PORT = 8765

# ─── آیپی VPS خارج (پنل اصلی) برای ping ─────────────────────
MAIN_VPS_IP = "YOUR_FOREIGN_VPS_IP"

# ─── @IranNodeBot ────────────────────────────────────────────
TELEGRAM_NODE = {
    "token":   "IRAN_NODE_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID",       # همان chat_id ربات اصلی
}

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

INTERVALS = {
    "monitor":         60,
    "periodic_report": 3600,
    "heartbeat":       180,
}

CRITICAL_SERVICES   = ["docker", "nginx", "xray"]
WIREGUARD_INTERFACE = "wg0"    # یا None
LOG_LINES           = 50
ALLOWED_IPS         = None
# ALLOWED_IPS = ["YOUR_FOREIGN_VPS_IP"]
