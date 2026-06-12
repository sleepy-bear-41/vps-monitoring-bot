#!/usr/bin/env python3
# ============================================================
#  monitor.py  —  @MainBot  |  اجرا روی VPS خارج (پنل اصلی)
#  - همه دستورات تلگرام
#  - Marzban مستقیم (localhost روی خارج)
#  - Poll از Agent نود ایران
#  - متریک‌های محلی خارج
#  - تمام features: jitter، RX/TX rate، users، tracker...
# ============================================================

import os, re, subprocess, sys, time, threading, logging
from datetime import datetime
from typing import Optional, Dict

import psutil
import requests
import schedule

from monitor_config import (
    IRAN_NODE_API, MARZBAN, TELEGRAM, THRESHOLDS,
    INTERVALS, LOCAL_SERVICES, LOG_LINES,
)
from tracker import NetworkTracker, MarzbanTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("monitor.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ─── Trackers ───────────────────────────────────────────────
net_tracker = NetworkTracker()
mzb_tracker = MarzbanTracker()

# ─── State ──────────────────────────────────────────────────
_alerts:        Dict[str, bool] = {}
_iran_node_data: Optional[dict] = None
_iran_last_ok:  float = 0.0

# ─── Marzban token ──────────────────────────────────────────
_mz_token:    Optional[str] = None
_mz_token_ts: float = 0.0
TOKEN_TTL = 1800

_last_update_id = 0


# ============================================================
#  Telegram
# ============================================================

def tg(text: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM['token']}/sendMessage",
            json={"chat_id": TELEGRAM["chat_id"], "text": text[:4096],
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10)
        return r.ok
    except Exception as e:
        log.error(f"Telegram: {e}"); return False


def tg_alert(key: str, condition: bool, msg: str) -> None:
    prev = _alerts.get(key, False)
    if condition and not prev:
        tg(f"🚨 <b>ALERT</b>\n{msg}"); _alerts[key] = True
    elif not condition and prev:
        tg(f"✅ <b>RESOLVED</b>\n{msg}"); _alerts[key] = False


# ============================================================
#  دریافت از Agent نود ایران
# ============================================================

def fetch_iran_node() -> Optional[dict]:
    global _iran_node_data, _iran_last_ok
    try:
        r = requests.get(
            IRAN_NODE_API["metrics_url"],
            headers={"X-API-Key": IRAN_NODE_API["api_key"]},
            timeout=IRAN_NODE_API["timeout"])
        if r.ok:
            _iran_node_data = r.json()
            _iran_last_ok   = time.time()
            return _iran_node_data
    except Exception as e:
        log.warning(f"Iran node agent: {e}")
    return None


def check_iran_heartbeat() -> None:
    if _iran_last_ok == 0: return
    elapsed = time.time() - _iran_last_ok
    tg_alert(
        "iran_heartbeat", elapsed > INTERVALS["heartbeat"],
        f"🇮🇷 نود ایران <b>{int(elapsed//60)} دقیقه</b> پاسخ نمی‌دهد!\n"
        f"آخرین ارتباط: {datetime.fromtimestamp(_iran_last_ok).strftime('%H:%M:%S')}",
    )


# ============================================================
#  متریک‌های محلی VPS خارج
# ============================================================

def collect_local_system() -> dict:
    cpu  = psutil.cpu_percent(interval=1)
    load = psutil.getloadavg()
    mem  = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    dio  = psutil.disk_io_counters()

    try:
        raw = subprocess.check_output(
            "df -i / | tail -1 | awk '{print $5}' | tr -d '%'",
            shell=True, timeout=5).decode().strip()
        inode_pct = int(raw) if raw.isdigit() else 0
    except Exception:
        inode_pct = 0

    cpu_temp: any = "N/A"
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for k in ("coretemp", "cpu_thermal", "acpitz", "k10temp"):
                if k in temps and temps[k]:
                    cpu_temp = round(temps[k][0].current, 1); break
        if cpu_temp == "N/A":
            raw = subprocess.check_output(
                "cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null",
                shell=True, timeout=3).decode().strip()
            if raw.isdigit(): cpu_temp = round(int(raw) / 1000, 1)
    except Exception:
        pass

    up = int(time.time() - psutil.boot_time())
    d, r = divmod(up, 86400); h, r = divmod(r, 3600); m, _ = divmod(r, 60)

    return {
        "cpu_percent":      cpu,
        "load_1":           round(load[0], 2),
        "load_5":           round(load[1], 2),
        "load_15":          round(load[2], 2),
        "ram_total_mb":     mem.total     // 1048576,
        "ram_used_mb":      mem.used      // 1048576,
        "ram_free_mb":      mem.available // 1048576,
        "ram_percent":      mem.percent,
        "swap_total_mb":    swap.total    // 1048576,
        "swap_used_mb":     swap.used     // 1048576,
        "swap_percent":     round(swap.percent, 1),
        "disk_total_gb":    round(disk.total / 1073741824, 1),
        "disk_used_gb":     round(disk.used  / 1073741824, 1),
        "disk_free_gb":     round(disk.free  / 1073741824, 1),
        "disk_percent":     disk.percent,
        "inode_percent":    inode_pct,
        "disk_io_read_mb":  round(dio.read_bytes  / 1048576, 1) if dio else 0,
        "disk_io_write_mb": round(dio.write_bytes / 1048576, 1) if dio else 0,
        "cpu_temp":         cpu_temp,
        "uptime":           f"{d}d {h}h {m}m",
    }


def collect_local_network() -> dict:
    iran_ip = re.search(r"https?://([^/:]+)", IRAN_NODE_API["metrics_url"])
    peer    = iran_ip.group(1) if iran_ip else ""

    conns = psutil.net_connections(kind="tcp")
    established = sum(1 for c in conns if c.status == "ESTABLISHED")
    time_wait   = sum(1 for c in conns if c.status == "TIME_WAIT")
    close_wait  = sum(1 for c in conns if c.status == "CLOSE_WAIT")
    nio = psutil.net_io_counters()

    ping_ms = packet_loss = jitter = -1.0
    if peer:
        try:
            raw = subprocess.check_output(
                f"ping -c 5 -W 2 {peer} 2>&1 | tail -2",
                shell=True, timeout=18).decode()
            m = re.search(r"(\d+\.?\d+)/(\d+\.?\d+)/(\d+\.?\d+)/(\d+\.?\d+)", raw)
            if m: ping_ms = float(m.group(2)); jitter = float(m.group(4))
            m2 = re.search(r"(\d+)%\s+packet loss", raw)
            if m2: packet_loss = float(m2.group(1))
        except Exception:
            pass

    return {
        "tcp_established":    established,
        "tcp_time_wait":      time_wait,
        "tcp_close_wait":     close_wait,
        "net_rx_gb":          round(nio.bytes_recv / 1073741824, 4),
        "net_tx_gb":          round(nio.bytes_sent / 1073741824, 4),
        "ping_to_iran_ms":    ping_ms,
        "packet_loss_pct":    packet_loss,
        "jitter_ms":          jitter,
    }


def collect_local_services() -> dict:
    result = {}
    for svc in LOCAL_SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=5)
            result[svc] = r.stdout.strip() == "active"
        except Exception:
            result[svc] = False
    return result


def collect_local_logs() -> dict:
    cmds = {
        "xray":    f"journalctl -u xray -n {LOG_LINES} --no-pager 2>/dev/null | grep -iE 'error|warn|fail' | tail -10",
        "marzban": f"journalctl -u marzban -n {LOG_LINES} --no-pager 2>/dev/null | grep -iE 'error|warn|fail' | tail -10",
        "nginx":   f"tail -n {LOG_LINES} /var/log/nginx/error.log 2>/dev/null | grep -vE '^\s*$' | tail -10",
        "docker":  f"journalctl -u docker -n {LOG_LINES} --no-pager 2>/dev/null | grep -iE 'error|fail' | tail -5",
    }
    logs = {}
    for svc, cmd in cmds.items():
        try:
            out = subprocess.check_output(
                cmd, shell=True, timeout=6).decode(errors="replace").strip()
            logs[svc] = out[:500] if out else ""
        except Exception:
            logs[svc] = ""
    return logs


# ============================================================
#  Marzban خارج (localhost)
# ============================================================

def _mz_auth() -> Optional[str]:
    global _mz_token, _mz_token_ts
    if _mz_token and (time.time() - _mz_token_ts) < TOKEN_TTL:
        return _mz_token
    try:
        r = requests.post(
            f"{MARZBAN['url']}/api/admin/token",
            data={"username": MARZBAN["user"], "password": MARZBAN["pass"]},
            timeout=10)
        _mz_token    = r.json().get("access_token")
        _mz_token_ts = time.time()
        return _mz_token
    except Exception as e:
        log.warning(f"Marzban token: {e}"); return None


def fetch_marzban_users() -> list:
    token = _mz_auth()
    if not token: return []
    try:
        r = requests.get(
            f"{MARZBAN['url']}/api/users?limit=500",
            headers={"Authorization": f"Bearer {token}"}, timeout=20)
        if r.status_code == 401:
            global _mz_token; _mz_token = None; return []
        return r.json().get("users", [])
    except Exception as e:
        log.warning(f"Marzban users: {e}"); return []


def fetch_marzban_summary() -> dict:
    token = _mz_auth()
    if not token: return {"error": "Marzban unreachable"}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        sys_r = requests.get(f"{MARZBAN['url']}/api/system",
                             headers=headers, timeout=10)
        if sys_r.status_code == 401:
            global _mz_token; _mz_token = None
            return {"error": "Auth expired"}
        sys_d  = sys_r.json()
        users  = requests.get(f"{MARZBAN['url']}/api/users?limit=500",
                              headers=headers, timeout=15).json().get("users", [])
        now    = time.time()
        expired = sum(1 for u in users if u.get("expire") and u["expire"] < now)
        online  = sum(1 for u in users if u.get("online_at") and
                      (now - (u["online_at"] or 0)) < 180)

        xray_conns = 0
        try:
            inb = requests.get(f"{MARZBAN['url']}/api/inbounds",
                               headers=headers, timeout=10).json()
            if isinstance(inb, list):
                xray_conns = sum(len(i.get("users", [])) for i in inb)
        except Exception:
            pass

        return {
            "users_total":        len(users),
            "users_online":       online,
            "users_active":       sys_d.get("users_active", 0),
            "users_expired":      expired,
            "incoming_bandwidth": sys_d.get("incoming_bandwidth", 0),
            "outgoing_bandwidth": sys_d.get("outgoing_bandwidth", 0),
            "total_bandwidth":    sys_d.get("total_bandwidth", 0),
            "xray_version":       sys_d.get("xray_version", "?"),
            "xray_connections":   xray_conns,
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
#  هشدارها
# ============================================================

def run_alerts(label: str, s: dict, n: dict, svc: dict) -> None:
    T = THRESHOLDS
    tg_alert(f"{label}_cpu",  s.get("cpu_percent",  0) > T["cpu_percent"],
             f"<b>{label}</b>\n🔥 CPU: {s.get('cpu_percent',0):.1f}%")
    tg_alert(f"{label}_ram",  s.get("ram_percent",  0) > T["ram_percent"],
             f"<b>{label}</b>\n💾 RAM: {s.get('ram_percent',0):.1f}%")
    tg_alert(f"{label}_disk", s.get("disk_percent", 0) > T["disk_percent"],
             f"<b>{label}</b>\n💿 Disk: {s.get('disk_percent',0):.1f}%")
    tg_alert(f"{label}_load", s.get("load_1", 0) > T["load_avg_1"],
             f"<b>{label}</b>\n⚡ Load: {s.get('load_1',0):.2f}")
    p = n.get("ping_to_iran_ms", n.get("ping_to_foreign_ms", -1))
    if p > 0:
        tg_alert(f"{label}_ping", p > T["ping_ms"],
                 f"<b>{label}</b>\n📡 Ping: {p:.1f}ms")
    loss = n.get("packet_loss_pct", -1)
    if loss >= 0:
        tg_alert(f"{label}_loss", loss > T["packet_loss"],
                 f"<b>{label}</b>\n📉 Packet Loss: {loss:.0f}%")
    temp = s.get("cpu_temp")
    if isinstance(temp, (int, float)):
        tg_alert(f"{label}_temp", temp > T["cpu_temp"],
                 f"<b>{label}</b>\n🌡️ Temp: {temp}°C")
    for name, up in svc.items():
        tg_alert(f"{label}_{name}", not up,
                 f"<b>{label}</b>\n⚠️ سرویس <b>{name}</b> DOWN!")


def check_expiring_alerts() -> None:
    if not mzb_tracker.is_fresh: return
    exp   = mzb_tracker.expiring_soon()
    count = len(exp["in_24h"])
    if count:
        names = ", ".join(u["username"] for u in exp["in_24h"][:5])
        tg_alert("expiring_24h", count > 0,
                 f"🌍 خارج\n⏰ <b>{count}</b> کاربر ۲۴h دیگر منقضی: {names}")


# ============================================================
#  فرمت‌بندی
# ============================================================

def _bar(pct: float, w: int = 10) -> str:
    f = int(pct / 100 * w)
    return "[" + "█" * f + "░" * (w - f) + "]"

def _ok(v: bool) -> str: return "🟢" if v else "🔴"

def _gb(b: int) -> str:
    if b >= 1073741824: return f"{b/1073741824:.2f} GB"
    if b >= 1048576:    return f"{b/1048576:.1f} MB"
    return f"{b/1024:.0f} KB"


def fmt_server(label: str, s: dict, n: dict, svc: dict, key: str) -> str:
    rate    = net_tracker.get_rate(key)
    win     = rate["window_min"]
    win_lbl = f"{win:.0f} min" if win > 0 else "در حال جمع‌آوری..."

    # تشخیص جهت ping بر اساس key موجود در dict
    if "ping_to_iran_ms" in n:
        ping_val, ping_lbl = n["ping_to_iran_ms"], "→ ایران"
    elif "ping_to_foreign_ms" in n:
        ping_val, ping_lbl = n["ping_to_foreign_ms"], "→ خارج"
    else:
        ping_val, ping_lbl = -1, ""

    loss = n.get("packet_loss_pct", -1)
    jit  = n.get("jitter_ms", -1)
    now  = datetime.now().strftime("%H:%M  %Y-%m-%d")

    lines = [
        f"<b>{'─'*32}</b>",
        f"<b>📊 {label}</b>  •  {now}",
        f"⏱ <code>{s.get('uptime','?')}</code>",
        "",
        "<b>💻 سرور</b>",
        f"  CPU   {s.get('cpu_percent',0):.1f}%  {_bar(s.get('cpu_percent',0))}",
        f"  Load  {s.get('load_1',0):.2f} / {s.get('load_5',0):.2f} / {s.get('load_15',0):.2f}",
        f"  RAM   {s.get('ram_used_mb',0):,} / {s.get('ram_total_mb',0):,} MB  ({s.get('ram_percent',0):.0f}%)",
        f"  Swap  {s.get('swap_used_mb',0)} / {s.get('swap_total_mb',0)} MB  ({s.get('swap_percent',0):.0f}%)",
        f"  Disk  {s.get('disk_used_gb',0)} / {s.get('disk_total_gb',0)} GB  ({s.get('disk_percent',0):.0f}%)",
        f"  Inode {s.get('inode_percent',0)}%  |  Temp {s.get('cpu_temp','N/A')}{'°C' if isinstance(s.get('cpu_temp'), float) else ''}",
        "",
        "<b>🌐 شبکه</b>",
        f"  Ping {ping_lbl}:   {'N/A' if ping_val < 0 else f'{ping_val:.1f} ms'}",
        f"  Packet Loss:    {'N/A' if loss < 0 else f'{loss:.0f}%'}",
        f"  Jitter:         {'N/A' if jit < 0 else f'{jit:.1f} ms'}",
        f"  ESTAB / TW / CW:  {n.get('tcp_established',0)} / {n.get('tcp_time_wait',0)} / {n.get('tcp_close_wait',0)}",
        "",
        f"<b>📈 ترافیک  <i>({win_lbl})</i></b>",
        f"  📥 RX: {rate['rx_mb_per_min']:.2f} MB/min",
        f"  📤 TX: {rate['tx_mb_per_min']:.2f} MB/min",
        f"  کل RX/TX: {n.get('net_rx_gb',0):.2f} / {n.get('net_tx_gb',0):.2f} GB",
        "",
        "<b>⚙️ سرویس‌ها</b>",
    ]
    for name, up in svc.items():
        lines.append(f"  {_ok(up)} {name}")
    return "\n".join(lines)


def fmt_tunnel(iran_net: dict, iran_wg: dict) -> str:
    p    = iran_net.get("ping_to_foreign_ms", -1)
    loss = iran_net.get("packet_loss_pct", -1)
    jit  = iran_net.get("jitter_ms", -1)
    lines = [
        "<b>🌉 کیفیت Tunnel  🇮🇷 ↔ 🌍</b>", "",
        f"  📡 تاخیر:       {'N/A' if p < 0 else f'{p:.1f} ms'}",
        f"  📉 Packet Loss: {'N/A' if loss < 0 else f'{loss:.0f}%'}",
        f"  📊 Jitter:      {'N/A' if jit < 0 else f'{jit:.1f} ms'}",
        "",
        f"  🔒 WireGuard: {iran_wg.get('status','?')}",
    ]
    if iran_wg.get("handshake"):
        lines.append(f"    Handshake: {iran_wg['handshake']}")
    if iran_wg.get("transfer"):
        lines.append(f"    Transfer:  {iran_wg['transfer']}")
    return "\n".join(lines)


def fmt_users() -> str:
    if not mzb_tracker.is_fresh:
        return "<b>👥 کاربران</b>\n⚠️ داده‌ای موجود نیست — در حال دریافت..."

    summ  = mzb_tracker.summary()
    new_c = mzb_tracker.new_users_count()
    top   = mzb_tracker.top_users(5)
    exp   = mzb_tracker.expiring_soon()
    sold  = mzb_tracker.sold_traffic()

    lines = [
        "<b>👥 کاربران — 🌍 خارج (پنل اصلی)</b>", "",
        "<b>📊 وضعیت کلی</b>",
        f"  👥 کل:               {summ['total']}",
        f"  🟢 آنلاین (< 3min):  {summ['online_now']}",
        f"  ⏱ فعال ۱۰ دقیقه:    {summ['active_10min']}",
        f"  ❌ منقضی‌شده:        {summ['expired']}",
        "",
        "<b>🆕 کاربران جدید</b>",
        f"  امروز:     {new_c['day']}",
        f"  این هفته:  {new_c['week']}",
        f"  این ماه:   {new_c['month']}",
        "",
        "<b>📦 حجم فروخته‌شده</b>",
        f"  کل:        {sold['all']:.1f} GB",
        f"  این ماه:   {sold['month']:.1f} GB",
        f"  این هفته:  {sold['week']:.1f} GB",
        f"  امروز:     {sold['day']:.1f} GB",
    ]

    if exp["in_24h"] or exp["in_7d"]:
        lines += ["", "<b>⏰ در حال انقضا</b>"]
        if exp["in_24h"]:
            names = "  |  ".join(
                f"{u['username']} ({u['remaining']})" for u in exp["in_24h"][:5])
            lines.append(f"  🔴 ۲۴h: {names}")
        if exp["in_7d"]:
            names = "  |  ".join(
                f"{u['username']} ({u['remaining']})" for u in exp["in_7d"][:5])
            lines.append(f"  🟡 ۷d:  {names}")

    if top:
        lines += ["", "<b>🏆 پرمصرف‌ترین‌ها</b>"]
        for i, u in enumerate(top, 1):
            pct   = f"  {u['percent']}%" if u["percent"] is not None else ""
            limit = f"/ {u['limit_gb']} GB" if u["limit_gb"] else "/ ∞"
            mark  = "🟢" if u["online"] else "⚫"
            lines.append(f"  {i}. {mark} {u['username']:20s} "
                         f"{u['used_gb']} {limit}{pct}")

    upd = datetime.fromtimestamp(summ["updated_at"]).strftime("%H:%M:%S") \
          if summ["updated_at"] else "?"
    lines += ["", f"<i>آخرین به‌روزرسانی: {upd}</i>"]
    return "\n".join(lines)


def fmt_vpn_quick() -> str:
    if not mzb_tracker.is_fresh:
        return "<b>🔐 VPN</b>\n⚠️ داده قدیمی — /users را بزنید."
    s    = mzb_tracker.summary()
    sold = mzb_tracker.sold_traffic()
    rate = net_tracker.get_rate("foreign")
    win  = rate["window_min"]
    mzb  = fetch_marzban_summary()
    lines = [
        "<b>🔐 VPN — 🌍 خارج (پنل اصلی)</b>", "",
        f"  👥 {s['total']} کاربر  |  🟢 {s['online_now']} آنلاین  |  ⏱ {s['active_10min']} (10min)",
        f"  ❌ منقضی: {s['expired']}",
    ]
    if "error" not in mzb:
        lines += [
            "",
            f"  📥 دریافت:  {_gb(mzb.get('incoming_bandwidth',0))}",
            f"  📤 ارسال:   {_gb(mzb.get('outgoing_bandwidth',0))}",
            f"  🔧 Xray:    v{mzb.get('xray_version','?')}  |  🔗 {mzb.get('xray_connections',0)} conn",
        ]
    lines += [
        "",
        "<b>📦 حجم فروخته‌شده</b>",
        f"  کل: {sold['all']:.1f} GB  |  ماه: {sold['month']:.1f} GB",
        f"  هفته: {sold['week']:.1f} GB  |  امروز: {sold['day']:.1f} GB",
        "",
        f"<b>📈 نرخ ترافیک  ({win:.0f} min)</b>",
        f"  📥 {rate['rx_mb_per_min']:.2f} MB/min  |  📤 {rate['tx_mb_per_min']:.2f} MB/min",
    ]
    return "\n".join(lines)


def _fmt_logs(label: str, logs: dict) -> str:
    lines = [f"<b>📋 لاگ‌ها — {label}</b>", ""]
    has_err = False
    for svc, content in logs.items():
        if content.strip():
            has_err = True
            lines += [f"<b>▸ {svc}:</b>", f"<code>{content[:300]}</code>", ""]
    if not has_err:
        lines.append("✅ خطایی یافت نشد")
    return "\n".join(lines)


# ============================================================
#  وظایف زمان‌بندی‌شده
# ============================================================

def job_poll_iran_node() -> None:
    log.info("Polling Iran node agent...")
    data = fetch_iran_node()
    check_iran_heartbeat()
    if data:
        net = data.get("network", {})
        net_tracker.add_sample("iran_node",
                               net.get("net_rx_gb", 0),
                               net.get("net_tx_gb", 0))
        run_alerts("🇮🇷 نود ایران",
                   data.get("system",   {}),
                   net,
                   data.get("services", {}))


def job_poll_local() -> None:
    log.info("Collecting foreign local metrics...")
    s   = collect_local_system()
    n   = collect_local_network()
    svc = collect_local_services()
    net_tracker.add_sample("foreign",
                           n.get("net_rx_gb", 0),
                           n.get("net_tx_gb", 0))
    run_alerts("🌍 خارج", s, n, svc)


def job_fetch_users() -> None:
    log.info("Fetching Marzban users...")
    users = fetch_marzban_users()
    if users:
        mzb_tracker.update(users)
    check_expiring_alerts()


def job_check_logs() -> None:
    log.info("Checking logs...")
    local_logs = collect_local_logs()
    if any(v.strip() for v in local_logs.values()):
        tg(_fmt_logs("🌍 VPS خارج", local_logs))
    if _iran_node_data and _iran_node_data.get("logs"):
        iran_logs = _iran_node_data["logs"]
        if any(v.strip() for v in iran_logs.values()):
            tg(_fmt_logs("🇮🇷 نود ایران", iran_logs))


def job_periodic_report() -> None:
    log.info("Sending periodic report...")
    parts = []

    # ─── خارج (محلی / پنل اصلی) ───
    s   = collect_local_system()
    n   = collect_local_network()
    svc = collect_local_services()
    parts.append(fmt_server("🌍 VPS خارج (اصلی)", s, n, svc, "foreign"))

    # ─── نود ایران ───
    data = fetch_iran_node() or _iran_node_data
    if data:
        parts.append(fmt_server("🇮🇷 نود ایران",
                                data.get("system",   {}),
                                data.get("network",  {}),
                                data.get("services", {}), "iran_node"))
        parts.append(fmt_tunnel(data.get("network",   {}),
                                data.get("wireguard", {})))
    else:
        parts.append("⚠️ <b>نود ایران در دسترس نیست</b>")

    tg("\n\n".join(parts))


def job_daily_report() -> None:
    log.info("Daily report...")
    tg(f"<b>📅 گزارش روزانه  {datetime.now().strftime('%Y-%m-%d')}</b>\n\n"
       + fmt_users())


# ============================================================
#  دستورات ربات
# ============================================================

def poll_commands() -> None:
    global _last_update_id
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM['token']}/getUpdates",
            params={"offset": _last_update_id + 1, "timeout": 5},
            timeout=10)
        for upd in r.json().get("result", []):
            _last_update_id = upd["update_id"]
            msg     = upd.get("message", {})
            text    = msg.get("text", "").strip().lower()
            chat_id = str(msg.get("chat", {}).get("id", ""))
            admins  = [str(x) for x in TELEGRAM.get("admin_ids", [])]
            if admins and chat_id not in admins: continue
            threading.Thread(target=dispatch, args=(text,), daemon=True).start()
    except Exception as e:
        log.warning(f"Poll: {e}")


def dispatch(cmd: str) -> None:
    cmds = {
        "/status":  lambda: (_cmd_foreign(), _cmd_iran()),
        "/وضعیت":  lambda: (_cmd_foreign(), _cmd_iran()),
        "/foreign": _cmd_foreign,
        "/خارج":    _cmd_foreign,
        "/iran":    _cmd_iran,
        "/ایران":   _cmd_iran,
        "/vpn":     lambda: tg(fmt_vpn_quick()),
        "/users":   _cmd_users,
        "/کاربران": _cmd_users,
        "/tunnel":  _cmd_tunnel,
        "/logs":    _cmd_logs,
        "/لاگ":     _cmd_logs,
        "/report":  job_periodic_report,
        "/گزارش":   job_periodic_report,
        "/help":    _cmd_help,
        "/start":   _cmd_help,
        "/راهنما":  _cmd_help,
    }
    fn = cmds.get(cmd)
    if fn: fn()


def _cmd_foreign() -> None:
    s   = collect_local_system()
    n   = collect_local_network()
    svc = collect_local_services()
    tg(fmt_server("🌍 VPS خارج (اصلی)", s, n, svc, "foreign"))


def _cmd_iran() -> None:
    data = fetch_iran_node() or _iran_node_data
    if not data:
        tg("⚠️ نود ایران پاسخ نمی‌دهد."); return
    tg(fmt_server("🇮🇷 نود ایران",
                  data.get("system",   {}),
                  data.get("network",  {}),
                  data.get("services", {}), "iran_node"))


def _cmd_users() -> None:
    if not mzb_tracker.is_fresh:
        tg("⏳ در حال دریافت کاربران...")
        job_fetch_users()
    tg(fmt_users())


def _cmd_tunnel() -> None:
    data = fetch_iran_node() or _iran_node_data
    if data:
        tg(fmt_tunnel(data.get("network",   {}),
                      data.get("wireguard", {})))
    else:
        tg("⚠️ اطلاعات Tunnel در دسترس نیست.")


def _cmd_logs() -> None:
    tg(_fmt_logs("🌍 VPS خارج", collect_local_logs()))
    data = fetch_iran_node() or _iran_node_data
    if data and data.get("logs"):
        tg(_fmt_logs("🇮🇷 نود ایران", data["logs"]))


def _cmd_help() -> None:
    tg(
        "🤖 <b>@MainBot — مانیتورینگ VPS</b>\n\n"
        "/status   —  وضعیت هر دو سرور\n"
        "/foreign  —  VPS خارج (پنل اصلی)\n"
        "/iran     —  نود ایران\n"
        "/vpn      —  خلاصه VPN + حجم فروش\n"
        "/users    —  گزارش کامل کاربران\n"
        "              ├ فعال ۱۰min / جدید روز|هفته|ماه\n"
        "              ├ حجم فروخته‌شده کل/ماه/هفته/امروز\n"
        "              ├ در حال انقضا (24h و 7d)\n"
        "              └ Top 5 پرمصرف\n"
        "/tunnel   —  کیفیت ارتباط ایران ↔ خارج\n"
        "/logs     —  خطاهای لاگ\n"
        "/report   —  گزارش کامل\n"
        "/help     —  این پیام\n\n"
        "📌 گزارش خودکار: هر ۱ ساعت\n"
        "📅 گزارش روزانه: ۲۳:۵۹\n"
        "🔔 @IranNodeBot هشدارهای نود ایران را مستقل ارسال می‌کند"
    )


# ============================================================
#  Main
# ============================================================

def main() -> None:
    log.info("🚀 Main Monitor starting on Foreign VPS...")
    tg("🚀 <b>@MainBot شروع به کار کرد</b>\n/help برای راهنما")

    schedule.every(INTERVALS["poll_iran_node"]).seconds.do(job_poll_iran_node)
    schedule.every(INTERVALS["poll_local"]).seconds.do(job_poll_local)
    schedule.every(INTERVALS["fetch_users"]).seconds.do(job_fetch_users)
    schedule.every(INTERVALS["log_check"]).seconds.do(job_check_logs)
    schedule.every(INTERVALS["periodic_report"]).seconds.do(job_periodic_report)
    schedule.every().day.at("23:59").do(job_daily_report)

    threading.Thread(target=job_fetch_users, daemon=True).start()
    threading.Thread(target=job_periodic_report, daemon=True).start()

    while True:
        try:
            schedule.run_pending()
            poll_commands()
        except KeyboardInterrupt:
            tg("⛔ @MainBot متوقف شد.")
            break
        except Exception as e:
            log.error(f"Main: {e}")
        time.sleep(5)


if __name__ == "__main__":
    main()
