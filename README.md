# 🖥️ VPS Monitor Bot — پنل اصلی روی خارج

```
VPS خارج (اصلی)               VPS ایران (نود)
┌──────────────────────┐      ┌─────────────────────┐
│ Marzban              │      │ agent.py :8765      │
│ monitor.py @MainBot  │◄poll─│ node_monitor.py     │
│ همه دستورات          │      │ @IranNodeBot        │
└──────────────────────┘      └─────────────────────┘
```

## ساختار
```
├── foreign/
│   ├── monitor.py                ← @MainBot
│   ├── tracker.py
│   ├── monitor_config.example.py
│   ├── requirements.txt
│   └── vps-monitor.service
├── iran/
│   ├── agent.py                  ← FastAPI metrics
│   ├── node_monitor.py           ← @IranNodeBot
│   ├── node_config.example.py
│   ├── requirements.txt
│   ├── vps-agent.service
│   └── vps-node-monitor.service
├── nginx_iran_agent.conf         ← روی VPS ایران
└── .gitignore
```

## نصب

### ۱. VPS ایران (نود)
```bash
mkdir /opt/vps-node && cd /opt/vps-node
pip install -r requirements.txt
cp node_config.example.py node_config.py && nano node_config.py

# SSL روی ایران
certbot --nginx -d YOUR_IRAN_DOMAIN
cp nginx_iran_agent.conf /etc/nginx/sites-available/agent
ln -s /etc/nginx/sites-available/agent /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

cp vps-agent.service vps-node-monitor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now vps-agent vps-node-monitor
```

### ۲. VPS خارج (اصلی)
```bash
mkdir /opt/vps-monitor && cd /opt/vps-monitor
pip install -r requirements.txt
cp monitor_config.example.py monitor_config.py && nano monitor_config.py

cp vps-monitor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now vps-monitor
```

### API Key
```bash
openssl rand -hex 32
```

## دستورات @MainBot
| دستور | توضیح |
|-------|-------|
| /status | هر دو سرور |
| /foreign | VPS خارج + RX/TX rate + Jitter |
| /iran | نود ایران + RX/TX rate + Jitter |
| /vpn | خلاصه VPN + حجم فروش |
| /users | کاربران کامل + sold traffic |
| /tunnel | کیفیت ارتباط + WireGuard |
| /logs | لاگ‌ها |
| /report | گزارش کامل |

## دستورات @IranNodeBot
| دستور | توضیح |
|-------|-------|
| /node | وضعیت نود |
| /logs | لاگ‌های نود |
