# -*- coding: utf-8 -*-
"""Oceń staging JSON: rank etapu + liczba rekordów (stdout: rank\\ttotal\\tstage)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

STAGE_RANK = {
    "po_scrape_kontakt": 40,
    "po_kontakt": 40,
    "po_maps": 30,
    "validated": 20,
    "discovery": 10,
}


def main() -> int:
    staging = Path(sys.argv[1])
    data = json.loads(staging.read_text(encoding="utf-8"))
    meta_stage = (data.get("stage") or "").strip().lower()
    sheets = data.get("sheets") or {}
    total = 0
    rec_rank = 0
    for rows in sheets.values():
        if not isinstance(rows, list):
            continue
        total += len(rows)
        for record in rows:
            if not isinstance(record, dict):
                continue
            stage = (record.get("pipeline_stage") or "").strip().lower()
            rec_rank = max(rec_rank, STAGE_RANK.get(stage, 0))
    rank = max(STAGE_RANK.get(meta_stage, 0), rec_rank)
    label = meta_stage or "?"
    print(f"{rank}\t{total}\t{label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
