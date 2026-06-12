#!/usr/bin/env python3
# ============================================================
#  node_monitor.py  —  @IranNodeBot  |  اجرا روی VPS خارج (نود)
#  کاملاً مستقل — حتی اگر VPS ایران قطع باشد کار می‌کند
#  دستورات: /node  /logs  /help
# ============================================================

import re, subprocess, sys, time, threading, logging
from datetime import datetime
from typing import Dict

import psutil
import requests
import schedule

from node_config import (
    TELEGRAM_NODE, THRESHOLDS, INTERVALS,
    CRITICAL_SERVICES, WIREGUARD_INTERFACE, LOG_LINES,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("node_monitor.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

_alerts: Dict[str, bool] = {}
_last_update_id = 0


# ============================================================
#  Telegram
# ============================================================

def tg(text: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_NODE['token']}/sendMessage",
            json={"chat_id": TELEGRAM_NODE["chat_id"], "text": text[:4096],
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10)
        return r.ok
    except Exception as e:
        log.error(f"Telegram: {e}"); return False


def tg_alert(key: str, condition: bool, msg: str) -> None:
    prev = _alerts.get(key, False)
    if condition and not prev:
        tg(f"🚨 <b>ALERT — 🇮🇷 نود ایران</b>\n{msg}"); _alerts[key] = True
    elif not condition and prev:
        tg(f"✅ <b>RESOLVED — 🇮🇷 نود ایران</b>\n{msg}"); _alerts[key] = False


# ============================================================
#  متریک‌های محلی نود
# ============================================================

def get_system() -> dict:
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
        "cpu_percent":  cpu,
        "load_1":       round(load[0], 2),
        "load_5":       round(load[1], 2),
        "load_15":      round(load[2], 2),
        "ram_total_mb": mem.total     // 1048576,
        "ram_used_mb":  mem.used      // 1048576,
        "ram_percent":  mem.percent,
        "swap_used_mb": swap.used     // 1048576,
        "swap_total_mb":swap.total    // 1048576,
        "swap_percent": round(swap.percent, 1),
        "disk_used_gb": round(disk.used  / 1073741824, 1),
        "disk_total_gb":round(disk.total / 1073741824, 1),
        "disk_percent": disk.percent,
        "inode_percent":inode_pct,
        "disk_io_r_mb": round(dio.read_bytes  / 1048576, 1) if dio else 0,
        "disk_io_w_mb": round(dio.write_bytes / 1048576, 1) if dio else 0,
        "cpu_temp":     cpu_temp,
        "uptime":       f"{d}d {h}h {m}m",
        "net_rx_gb":    round(psutil.net_io_counters().bytes_recv / 1073741824, 3),
        "net_tx_gb":    round(psutil.net_io_counters().bytes_sent / 1073741824, 3),
    }


def get_network() -> dict:
    conns = psutil.net_connections(kind="tcp")
    established = sum(1 for c in conns if c.status == "ESTABLISHED")
    time_wait   = sum(1 for c in conns if c.status == "TIME_WAIT")
    close_wait  = sum(1 for c in conns if c.status == "CLOSE_WAIT")

    from node_config import MAIN_VPS_IP
    ping_ms = packet_loss = jitter = -1.0
    try:
        raw = subprocess.check_output(
            f"ping -c 5 -W 2 {MAIN_VPS_IP} 2>&1 | tail -2",
            shell=True, timeout=18).decode()
        m = re.search(r"(\d+\.?\d+)/(\d+\.?\d+)/(\d+\.?\d+)/(\d+\.?\d+)", raw)
        if m: ping_ms = float(m.group(2)); jitter = float(m.group(4))
        m2 = re.search(r"(\d+)%\s+packet loss", raw)
        if m2: packet_loss = float(m2.group(1))
    except Exception:
        pass

    return {
        "tcp_established": established,
        "tcp_time_wait":   time_wait,
        "tcp_close_wait":  close_wait,
        "ping_to_iran_ms": ping_ms,
        "packet_loss_pct": packet_loss,
        "jitter_ms":       jitter,
    }


def get_services() -> dict:
    result = {}
    for svc in CRITICAL_SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=5)
            result[svc] = r.stdout.strip() == "active"
        except Exception:
            result[svc] = False
    return result


def get_wireguard() -> dict:
    if not WIREGUARD_INTERFACE:
        return {"configured": False}
    try:
        raw = subprocess.check_output(
            f"wg show {WIREGUARD_INTERFACE} 2>/dev/null",
            shell=True, timeout=5).decode().strip()
        if not raw: return {"configured": True, "status": "down"}
        hs = re.search(r"latest handshake: (.+)", raw)
        tr = re.search(r"transfer: (.+)", raw)
        return {"configured": True, "status": "up",
                "handshake": hs.group(1).strip() if hs else "",
                "transfer":  tr.group(1).strip() if tr else ""}
    except Exception:
        return {"configured": True, "status": "error"}


def get_logs() -> dict:
    cmds = {
        "xray":   f"journalctl -u xray -n {LOG_LINES} --no-pager 2>/dev/null | grep -iE 'error|warn|fail' | tail -10",
        "nginx":  f"tail -n {LOG_LINES} /var/log/nginx/error.log 2>/dev/null | grep -vE '^\s*$' | tail -10",
        "docker": f"journalctl -u docker -n {LOG_LINES} --no-pager 2>/dev/null | grep -iE 'error|fail' | tail -5",
    }
    logs = {}
    for svc, cmd in cmds.items():
        try:
            out = subprocess.check_output(cmd, shell=True, timeout=6).decode(errors="replace").strip()
            logs[svc] = out[:500] if out else ""
        except Exception:
            logs[svc] = ""
    return logs


# ============================================================
#  هشدارها
# ============================================================

def run_alerts(s: dict, n: dict, svc: dict) -> None:
    T = THRESHOLDS
    tg_alert("cpu",  s.get("cpu_percent",  0) > T["cpu_percent"],
             f"🔥 CPU: {s.get('cpu_percent',0):.1f}%")
    tg_alert("ram",  s.get("ram_percent",  0) > T["ram_percent"],
             f"💾 RAM: {s.get('ram_percent',0):.1f}%")
    tg_alert("disk", s.get("disk_percent", 0) > T["disk_percent"],
             f"💿 Disk: {s.get('disk_percent',0):.1f}%")
    tg_alert("load", s.get("load_1", 0) > T["load_avg_1"],
             f"⚡ Load: {s.get('load_1',0):.2f}")
    p = n.get("ping_to_iran_ms", -1)
    if p > 0:
        tg_alert("ping", p > T["ping_ms"], f"📡 Ping → ایران: {p:.1f}ms")
    loss = n.get("packet_loss_pct", -1)
    if loss >= 0:
        tg_alert("loss", loss > T["packet_loss"], f"📉 Packet Loss: {loss:.0f}%")
    temp = s.get("cpu_temp")
    if isinstance(temp, (int, float)):
        tg_alert("temp", temp > T["cpu_temp"], f"🌡️ Temp: {temp}°C")
    for name, up in svc.items():
        tg_alert(f"svc_{name}", not up, f"⚠️ سرویس <b>{name}</b> DOWN!")


# ============================================================
#  فرمت‌بندی
# ============================================================

def _bar(pct: float, w: int = 10) -> str:
    f = int(pct / 100 * w)
    return "[" + "█" * f + "░" * (w - f) + "]"


def _ok(v: bool) -> str:
    return "🟢" if v else "🔴"


def fmt_node_status(s: dict, n: dict, svc: dict, wg: dict) -> str:
    p    = n.get("ping_to_iran_ms", -1)
    loss = n.get("packet_loss_pct", -1)
    jit  = n.get("jitter_ms", -1)
    now  = datetime.now().strftime("%H:%M  %Y-%m-%d")
    lines = [
        f"<b>{'─'*32}</b>",
        f"<b>🇮🇷 نود ایران VPS</b>  •  {now}",
        f"⏱ <code>{s.get('uptime','?')}</code>",
        "",
        "<b>💻 سرور</b>",
        f"  CPU   {s.get('cpu_percent',0):.1f}%  {_bar(s.get('cpu_percent',0))}",
        f"  Load  {s.get('load_1',0):.2f} / {s.get('load_5',0):.2f} / {s.get('load_15',0):.2f}",
        f"  RAM   {s.get('ram_used_mb',0):,} / {s.get('ram_total_mb',0):,} MB  ({s.get('ram_percent',0):.0f}%)",
        f"  Swap  {s.get('swap_used_mb',0)} / {s.get('swap_total_mb',0)} MB  ({s.get('swap_percent',0):.0f}%)",
        f"  Disk  {s.get('disk_used_gb',0)} / {s.get('disk_total_gb',0)} GB  ({s.get('disk_percent',0):.0f}%)",
        f"  Inode {s.get('inode_percent',0)}%  |  Temp {s.get('cpu_temp','N/A')}{'°C' if isinstance(s.get('cpu_temp'), float) else ''}",
        f"  I/O   R:{s.get('disk_io_r_mb',0)} MB  W:{s.get('disk_io_w_mb',0)} MB",
        "",
        "<b>🌐 شبکه</b>",
        f"  Ping → ایران:  {'N/A' if p < 0 else f'{p:.1f} ms'}",
        f"  Packet Loss:   {'N/A' if loss < 0 else f'{loss:.0f}%'}",
        f"  Jitter:        {'N/A' if jit < 0 else f'{jit:.1f} ms'}",
        f"  ESTAB / TW / CW:  {n.get('tcp_established',0)} / {n.get('tcp_time_wait',0)} / {n.get('tcp_close_wait',0)}",
        f"  RX / TX:  {s.get('net_rx_gb',0):.2f} / {s.get('net_tx_gb',0):.2f} GB",
        "",
        "<b>⚙️ سرویس‌ها</b>",
    ]
    for name, up in svc.items():
        lines.append(f"  {_ok(up)} {name}")

    if wg.get("configured"):
        lines += ["", f"<b>🔒 WireGuard:</b> {wg.get('status','?')}"]
        if wg.get("handshake"):
            lines.append(f"  Handshake: {wg['handshake']}")

    return "\n".join(lines)


# ============================================================
#  وظایف زمان‌بندی‌شده
# ============================================================

def job_monitor() -> None:
    log.info("Node monitor check...")
    s   = get_system()
    n   = get_network()
    svc = get_services()
    run_alerts(s, n, svc)


def job_periodic_report() -> None:
    log.info("Node periodic report...")
    s   = get_system()
    n   = get_network()
    svc = get_services()
    wg  = get_wireguard()
    tg(fmt_node_status(s, n, svc, wg))


# ============================================================
#  دستورات ربات
# ============================================================

def poll_commands() -> None:
    global _last_update_id
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_NODE['token']}/getUpdates",
            params={"offset": _last_update_id + 1, "timeout": 5},
            timeout=10)
        for upd in r.json().get("result", []):
            _last_update_id = upd["update_id"]
            text = upd.get("message", {}).get("text", "").strip().lower()
            threading.Thread(target=dispatch, args=(text,), daemon=True).start()
    except Exception as e:
        log.warning(f"Poll: {e}")


def dispatch(cmd: str) -> None:
    if cmd in ("/node", "/status", "/وضعیت"):
        s   = get_system()
        n   = get_network()
        svc = get_services()
        wg  = get_wireguard()
        tg(fmt_node_status(s, n, svc, wg))
    elif cmd in ("/logs", "/لاگ"):
        logs = get_logs()
        lines = [f"<b>📋 لاگ‌ها — 🇮🇷 نود ایران</b>", ""]
        has_err = False
        for svc_name, content in logs.items():
            if content.strip():
                has_err = True
                lines += [f"<b>▸ {svc_name}:</b>", f"<code>{content[:300]}</code>", ""]
        if not has_err:
            lines.append("✅ خطایی یافت نشد")
        tg("\n".join(lines))
    elif cmd in ("/help", "/start", "/راهنما"):
        tg(
            "🤖 <b>@IranNodeBot — مانیتورینگ Node</b>\n\n"
            "/node   —  وضعیت کامل نود\n"
            "/logs   —  خطاهای لاگ\n"
            "/help   —  این پیام\n\n"
            "📌 گزارش خودکار هر ساعت\n"
            "🔔 هشدارهای لحظه‌ای مستقل از ربات ایران"
        )


# ============================================================
#  Main
# ============================================================

def main() -> None:
    log.info("🚀 Node Monitor starting...")
    tg("🚀 <b>Node Monitor شروع به کار کرد</b>\n/help برای راهنما")

    schedule.every(INTERVALS["monitor"]).seconds.do(job_monitor)
    schedule.every(INTERVALS["periodic_report"]).seconds.do(job_periodic_report)

    # اولین گزارش فوری
    threading.Thread(target=job_periodic_report, daemon=True).start()

    while True:
        try:
            schedule.run_pending()
            poll_commands()
        except KeyboardInterrupt:
            tg("⛔ Node Monitor متوقف شد.")
            break
        except Exception as e:
            log.error(f"Main: {e}")
        time.sleep(5)


if __name__ == "__main__":
    main()
