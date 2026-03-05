#!/usr/bin/env python3
import argparse
import configparser
import logging
import os
import smtplib
import subprocess
import sys
import json

from datetime import datetime, timedelta

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

DEFAULT_CONFIG = {
    "monitor": {
        "state_file": "/var/lib/mount_monitor/last",
        "alert_interval_hours": "1",
        "ignore_fstypes": "swap,proc,sysfs,devtmpfs,devpts,tmpfs,cgroup,cgroup2,pstore,debugfs,securityfs,hugetlbfs,mqueue,fusectl,efivarfs",
        "ignore_mountpoints": "none",
    },
    "email": {
        "enabled": "true",
        "smtp_host": "smtp.google.com",
        "smtp_port": "587",
        "smtp_use_tls": "true",
        "smtp_user": "",
        "smtp_password": "",
        "from_addr": "",
        "to_addr": "",
        "subject_prefix": "[Mount Alert]",
    },
}


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("mount_monitor")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # stdout handler (INFO and below)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(lambda record: record.levelno < logging.WARNING)
    stdout_handler.setFormatter(fmt)

    # stderr handler (WARNING and above)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(fmt)

    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)

    return logger


# mount checks


def parse_fstab(path: str = "/etc/fstab") -> list[dict]:
    """Return list of fstab entries (dicts with device, mountpoint, fstype)."""
    entries: list[dict] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                # skip comments or newlines
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                # < 4 parts means not what we want
                if len(parts) < 4:
                    continue
                entries.append(
                    {
                        "device": parts[0],
                        "mountpoint": parts[1],
                        "fstype": parts[2],
                        "options": parts[3],
                        "raw": line,
                    }
                )
    except FileNotFoundError:
        pass

    return entries


def get_active_mounts() -> set[str]:
    """Return set of currently mounted mountpoints from /proc/mounts."""
    mounts: set[str] = set()
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mounts.add(parts[1])
    except FileNotFoundError:
        # fallback, use `mount` command
        result = subprocess.run(["mount"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            # "device on mountpoint type fstype (options)"
            parts = line.split()
            if len(parts) >= 3 and parts[1] == "on":
                mounts.add(parts[2])
    return mounts


def check_mounts(
    ignore_fstypes: set[str],
    ignore_mountpoints: set[str],
    logger: logging.Logger,
) -> list[dict]:
    """
    Compare fstab entries against active mounts.
    Returns list of fstab entries that should be mounted but aren't.
    """
    fstab = parse_fstab()
    active = get_active_mounts()
    missing: list[dict] = []

    for entry in fstab:
        if entry["fstype"].lower() in ignore_fstypes:
            continue

        if entry["mountpoint"] in ignore_mountpoints:
            continue

        # skip special pseudo-mountpoints
        if entry["mountpoint"] in ("none", "swap"):
            continue

        if entry["mountpoint"] not in active:
            logger.warning("NOT MOUNTED: %s → %s", entry["device"], entry["mountpoint"])
            missing.append(entry)
        else:
            logger.debug("OK: %s → %s", entry["device"], entry["mountpoint"])

    return missing


# state management


def load_state(state_file: str) -> dict:
    try:
        with open(state_file) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state_file: str, state: dict) -> None:
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def should_alert(state: dict, interval_hours: float) -> bool:
    last_alert_str = state.get("last_alert_at")
    if not last_alert_str:
        return True
    last_alert = datetime.fromisoformat(last_alert_str)
    return datetime.now() - last_alert >= timedelta(hours=interval_hours)


# notifications


def send_email(
    cfg: configparser.SectionProxy,
    missing: list[dict],
    hostname: str,
    logger: logging.Logger,
) -> bool:
    if not cfg.getboolean("enabled", fallback=False):
        return False

    to_addr = cfg.get("to_addr", "").strip()
    from_addr = cfg.get("from_addr", "").strip()
    if not to_addr or not from_addr:
        logger.error("Email enabled but to_addr/from_addr not configured.")
        return False

    subject = (
        f"{cfg.get('subject_prefix', '[Mount Alert]')} Missing mounts on {hostname}"
    )

    lines = [
        f"Mount Monitor Alert — {hostname}",
        f"Detected at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"The following {len(missing)} fstab mount(s) are NOT currently mounted:",
        "",
    ]
    for e in missing:
        lines.append(f"  • {e['mountpoint']}  ({e['device']}, {e['fstype']})")
    lines += [
        "",
        "Please check the drive/connection and remount as needed.",
        "This alert will repeat every hour until the issue is resolved.",
    ]
    body = "\n".join(lines)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "plain"))

    try:
        port = cfg.getint("smtp_port", 587)
        use_tls = cfg.getboolean("smtp_use_tls", True)
        smtp = smtplib.SMTP(cfg.get("smtp_host", ""), port, timeout=15)

        if use_tls:
            smtp.starttls()

        user = cfg.get("smtp_user", "").strip()
        pw = cfg.get("smtp_password", "").strip()

        if user and pw:
            smtp.login(user, pw)

        smtp.sendmail(from_addr, [to_addr], msg.as_string())
        smtp.quit()

        logger.info("Email alert sent to %s", to_addr)
        return True

    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False


def main():

    parser = argparse.ArgumentParser(
        description="Monitor fstab mounts and alert if any missing."
    )

    parser.add_argument(
        "--config", default="/etc/mount_monitor.ini", help="Path to config file"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check mounts but don't send alerts or update state",
    )

    args = parser.parse_args()

    # load config and merge defaults
    cfg = configparser.ConfigParser()
    for section, values in DEFAULT_CONFIG.items():
        cfg[section] = values

    if os.path.exists(args.config):
        cfg.read(args.config)

    mon = cfg["monitor"]

    logger = setup_logging()
    hostname = os.uname().nodename

    ignore_fstypes = {
        x.strip().lower() for x in mon.get("ignore_fstypes", "").split(",") if x.strip()
    }
    ignore_mountpoints = {
        x.strip()
        for x in mon.get("ignore_mountpoints", "").split(",")
        if x.strip() and x.strip() != "none"
    }
    interval_hours = float(mon.get("alert_interval_hours", "1"))
    state_file = mon.get("state_file", "")

    logger.info("Starting mount check on %s", hostname)

    missing = check_mounts(ignore_fstypes, ignore_mountpoints, logger)

    if not missing:
        logger.info("All fstab mounts are active.")

        # clear any stored alert state so next failure alerts immediately
        state = load_state(state_file)
        if state.get("last_alert_at") and not args.dry_run:
            state = {}
            save_state(state_file, state)

        return 0

    logger.warning(
        "%d missing mount(s): %s", len(missing), [e["mountpoint"] for e in missing]
    )

    if args.dry_run:
        logger.info("Dry-run mode: skipping alert and state update.")
        return 1

    state = load_state(state_file)

    if not should_alert(state, interval_hours):
        last = state.get("last_alert_at", "unknown")
        logger.info(
            "Alert suppressed (last sent at %s, interval=%.1fh).", last, interval_hours
        )

        return 1

    # send notifications
    alerted = False
    alerted |= send_email(cfg["email"], missing, hostname, logger)

    if not alerted:
        logger.warning("No notification channels succeeded (check config and logs).")

    # update state regardless so we don't spam on misconfigured notifiers
    state["last_alert_at"] = datetime.now().isoformat()
    state["missing_mounts"] = [e["mountpoint"] for e in missing]
    save_state(state_file, state)

    return 1


if __name__ == "__main__":
    sys.exit(main())
