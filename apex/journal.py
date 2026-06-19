"""Append every closed trade to a CSV compatible with the Apex workbook."""
import csv, os
from datetime import datetime, timezone

HEADER = ["timestamp", "symbol", "side", "entry", "stop", "take",
          "exit", "size", "pnl", "r_multiple", "reason", "equity_after"]


class Journal:
    def __init__(self, path="apex_trades.csv"):
        self.path = path
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(HEADER)

    def record(self, **row):
        row.setdefault("timestamp", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        with open(self.path, "a", newline="") as f:
            csv.writer(f).writerow([row.get(k, "") for k in HEADER])
