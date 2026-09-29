"""Persistent daily collector: immediate bootstrap, then 10:00 in config timezone."""
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from radar import ROOT, config


def next_daily(current):
    target = current.replace(hour=10, minute=0, second=0, microsecond=0)
    return target if target > current else target + timedelta(days=1)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    zone = timezone(timedelta(hours=config()["timezone_offset_hours"]))
    state_path = ROOT / "data" / "scheduler.json"
    state_path.parent.mkdir(exist_ok=True)
    current = datetime.now(zone)
    state = json.loads(state_path.read_text()) if state_path.exists() else {"due": current.isoformat(), "attempt": 0}
    while True:
        current = datetime.now(zone)
        due = datetime.fromisoformat(state["due"])
        if current < due:
            time.sleep(min(30, (due - current).total_seconds()))
            continue
        # Persist the upcoming retry first so restarting a crashed container cannot spin.
        attempt = state["attempt"] + 1
        state = {"due": (current + timedelta(minutes=15)).isoformat(), "attempt": attempt}
        if attempt >= 4:
            state = {"due": next_daily(current).isoformat(), "attempt": 0}
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state)); tmp.replace(state_path)
        try:
            result = subprocess.run([sys.executable, str(ROOT / "radar.py"), "collect"], cwd=ROOT, timeout=2700)
            success = result.returncode == 0
        except subprocess.TimeoutExpired:
            logging.exception("Collection exceeded 45 minutes")
            success = False
        if success or attempt >= 4:
            state = {"due": next_daily(datetime.now(zone)).isoformat(), "attempt": 0}
        else:
            state["due"] = (datetime.now(zone) + timedelta(minutes=15)).isoformat()
        tmp.write_text(json.dumps(state)); tmp.replace(state_path)
        logging.info("Collection success=%s; next run %s", success, state["due"])


if __name__ == "__main__":
    main()
