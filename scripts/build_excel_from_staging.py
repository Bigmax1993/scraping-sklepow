# -*- coding: utf-8 -*-
"""Zbuduj neueroeffnung_wynik.xlsx ze staging JSON (bez Claude/mail)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neueroeffnung_scraper import (  # noqa: E402
    DATA_SHEET_NAMES,
    OUTPUT_FILE,
    STAGING_FILE,
    record_from_dict,
    write_excel,
)
from pipeline_state import load_staging  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logger = logging.getLogger("build_excel_from_staging")
    if not STAGING_FILE.is_file():
        logger.error("Brak pliku staging: %s", STAGING_FILE)
        return 1
    sheets = load_staging(
        STAGING_FILE,
        logger,
        data_sheet_names=DATA_SHEET_NAMES,
        record_from_dict=record_from_dict,
    )
    total = sum(len(v) for v in sheets.values())
    if total == 0:
        logger.error("Staging pusty — brak rekordów do Excela.")
        return 1
    for name in DATA_SHEET_NAMES:
        logger.info("  %s: %s", name, len(sheets.get(name, [])))
    write_excel(sheets, [], OUTPUT_FILE, logger)
    logger.info("Gotowe: %s (%s bajtów)", OUTPUT_FILE.resolve(), OUTPUT_FILE.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
