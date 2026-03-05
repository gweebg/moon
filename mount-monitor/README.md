# mount-monitor

A lightweight Python script that checks whether all fstab entries are currently mounted and sends an alert if any are missing.

Alerts are throttled to once per hour by default to avoid notification spam during extended outages.


## Requirements

- Python 3.8+
- An SMTP relay or mail server


## Installation

```bash
# copy the script
cp mount_monitor.py /opt/scripts/mount_monitor.py
chmod +x /opt/scripts/mount_monitor.py

# copy and edit the config
cp mount_monitor.ini.example /etc/mount_monitor.ini

# create the state directory
mkdir -p /var/lib/mount_monitor
```


## Configuration

All configuration lives in `/etc/mount_monitor.ini`. The example file contains every available option with comments.

The main sections are:

**Monitor**

| Key | Default | Description |
|-----|---------|-------------|
| `state_file` | `/var/lib/mount_monitor/last_alert.json` | Stores the timestamp of the last alert |
| `alert_interval_hours` | `1` | Minimum hours between repeated alerts |
| `ignore_fstypes` | `swap,proc,tmpfs,...` | Filesystem types to skip |
| `ignore_mountpoints` | `none` | Specific mountpoints to always skip |
| `log_file` | `/var/log/mount_monitor.log` | Log output path |

**Email**

| Key | Description |
|-----|-------------|
| `enabled` | `true` or `false` |
| `smtp_host` | Your SMTP relay hostname |
| `smtp_port` | Usually `25` or `587` |
| `smtp_use_tls` | `true` or `false` |
| `smtp_user` / `smtp_password` | Leave blank if your relay does not require authentication |
| `from_addr` / `to_addr` | Sender and recipient addresses |


## Usage

Run manually to verify everything is working:

```bash
# check mounts and send alerts if needed
python3 /opt/scripts/mount_monitor.py

# check mounts without sending alerts or updating state
python3 /opt/scripts/mount_monitor.py --dry-run

# use a non-default config file
python3 /opt/scripts/mount_monitor.py --config /path/to/custom.ini
```

### Cron Setup

Add an entry to run the check periodically. Every 10 minutes is a reasonable interval:

```
*/10 * * * * /usr/bin/python3 /opt/scripts/mount_monitor.py
```

Add via `crontab -e` for the root user, or drop a file in `/etc/cron.d/`:

```bash
echo '*/10 * * * * root /usr/bin/python3 /opt/scripts/mount_monitor.py' > /etc/cron.d/mount_monitor
```

### Systemd Setup

As an alternative to cron, you can run the script via a systemd service and timer. This integrates with `journalctl` and gives you finer control over dependencies and execution policy.

Create the service unit at `/etc/systemd/system/mount_monitor.service`:

```ini
[Unit]
Description=Mount Monitor - check fstab mounts and alert if missing
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/scripts/mount_monitor.py
StandardOutput=journal
StandardError=journal
```

Create the timer unit at `/etc/systemd/system/mount_monitor.timer`:

```ini
[Unit]
Description=Run mount_monitor every 10 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start the timer:

```bash
systemctl daemon-reload
systemctl enable --now mount_monitor.timer

# verify the timer is scheduled
systemctl list-timers mount_monitor.timer

# check recent output
journalctl -u mount_monitor.service -n 50
```

To trigger a run immediately without waiting for the timer:

```bash
systemctl start mount_monitor.service
```