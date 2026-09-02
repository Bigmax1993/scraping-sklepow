"""
Wspólny stan pipeline'u segmentowego — staging JSON, merge, limity czasu.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from neueroeffnung_scraper import Record

SCRIPT_DIR = Path(__file__).resolve().parent
STAGING_FILE = SCRIPT_DIR / "neueroeffnung_staging.json"

STAGE_DISCOVERY = "discovery"
STAGE_VALIDATED = "validated"
STAGE_PO_MAPS = "po_maps"
STAGE_PO_SCRAPE_KONTAKT = "po_scrape_kontakt"
STAGE_PO_KONTAKT = "po_kontakt"

STAGE_ORDER = {
    STAGE_DISCOVERY: 0,
    STAGE_VALIDATED: 1,
    STAGE_PO_MAPS: 2,
    STAGE_PO_SCRAPE_KONTAKT: 3,
    STAGE_PO_KONTAKT: 4,
}

DEFAULT_MAX_RUNTIME_SECONDS = 4 * 3600  # 4 h
DEFAULT_MAPS_BATCH_LIMIT = 200
DEFAULT_CONTACT_BATCH_LIMIT = 250


class StageResult:
    """Wynik segmentu pipeline — limit czasu nie jest błędem fatalnym."""

    __slots__ = ("time_limit_hit", "error")

    def __init__(self, *, time_limit_hit: bool = False, error: str | None = None):
        self.time_limit_hit = time_limit_hit
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None


class RuntimeBudget:
    """Twardy limit czasu runu — stop gdy przekroczony (bez wyjątku)."""

    def __init__(self, max_seconds: int | None = None):
        if max_seconds is None:
            max_seconds = int(os.environ.get("MAX_RUNTIME_SECONDS", str(DEFAULT_MAX_RUNTIME_SECONDS)))
        self.max_seconds = max(1, max_seconds)
        self.started_at = time.monotonic()
        self.time_limit_hit = False

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def remaining(self) -> float:
        return max(0.0, self.max_seconds - self.elapsed)

    def expired(self) -> bool:
        return self.elapsed >= self.max_seconds

    def check(self, logger) -> bool:
        """Zwraca False gdy czas minął — ustawia time_limit_hit, bez wyjątku."""
        if self.expired():
            if not self.time_limit_hit:
                self.time_limit_hit = True
                logger.warning(
                    "Limit czasu runu (%s s) osiągnięty po %.0f s — zatrzymuję segment (postęp zostanie zapisany).",
                    self.max_seconds,
                    self.elapsed,
                )
            return False
        return True


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def maps_batch_limit() -> int:
    return env_int("MAPS_BATCH_LIMIT", DEFAULT_MAPS_BATCH_LIMIT)


def contact_batch_limit() -> int:
    return env_int("CONTACT_BATCH_LIMIT", DEFAULT_CONTACT_BATCH_LIMIT)


def empty_sheets(data_sheet_names: tuple[str, ...]) -> dict[str, list]:
    return {name: [] for name in data_sheet_names}


def load_staging(
    path: Path,
    logger,
    *,
    data_sheet_names: tuple[str, ...],
    record_from_dict: Callable[[dict], Record],
) -> dict[str, list[Record]]:
    if not path.exists():
        logger.info("Brak staging — start od pustego stanu.")
        return empty_sheets(data_sheet_names)
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        sheets: dict[str, list[Record]] = {}
        for name in data_sheet_names:
            sheets[name] = [
                record_from_dict(item) for item in payload.get("sheets", {}).get(name, [])
            ]
        total = sum(len(v) for v in sheets.values())
        logger.info(
            "Wczytano staging (%s): %s rekordów, stage=%s",
            path.name,
            total,
            payload.get("stage", "?"),
        )
        return sheets
    except Exception as exc:
        logger.warning("Nie wczytano staging — reset: %s", exc)
        return empty_sheets(data_sheet_names)


def save_staging(
    sheets: dict[str, list[Record]],
    path: Path,
    logger,
    *,
    stage: str,
    data_sheet_names: tuple[str, ...],
    record_to_dict: Callable[[Record], dict],
    extra: dict | None = None,
) -> None:
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "stage": stage,
        "sheets": {
            name: [record_to_dict(record) for record in sheets.get(name, [])]
            for name in data_sheet_names
        },
    }
    if extra:
        payload.update(extra)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    total = sum(len(sheets.get(name, [])) for name in data_sheet_names)
    logger.info("Zapisano staging (%s): %s rekordów, stage=%s", path.name, total, stage)


def staging_index(
    sheets: dict[str, list[Record]],
    data_sheet_names: tuple[str, ...],
    fingerprint_fn: Callable[[Record], str],
) -> dict[str, tuple[str, int, Record]]:
    index: dict[str, tuple[str, int, Record]] = {}
    for category_name in data_sheet_names:
        for idx, record in enumerate(sheets.get(category_name, [])):
            index[fingerprint_fn(record)] = (category_name, idx, record)
    return index


def merge_discovery_sheets(
    staging: dict[str, list[Record]],
    discovered: dict[str, list[Record]],
    data_sheet_names: tuple[str, ...],
    fingerprint_fn: Callable[[Record], str],
    processed: set[str],
    logger,
) -> dict[str, list[Record]]:
    """Dodaje nowe rekordy discovery; nie nadpisuje rekordów w zaawansowanym etapie."""
    merged = {name: list(staging.get(name, [])) for name in data_sheet_names}
    index = staging_index(merged, data_sheet_names, fingerprint_fn)

    added = 0
    refreshed = 0
    skipped_processed = 0
    skipped_advanced = 0

    for category_name in data_sheet_names:
        for new_record in discovered.get(category_name, []):
            fp = fingerprint_fn(new_record)
            if fp in processed:
                skipped_processed += 1
                continue

            new_record.pipeline_stage = STAGE_DISCOVERY

            if fp not in index:
                merged[category_name].append(new_record)
                index[fp] = (category_name, len(merged[category_name]) - 1, new_record)
                added += 1
                continue

            cat, idx, existing = index[fp]
            if existing.pipeline_stage != STAGE_DISCOVERY:
                skipped_advanced += 1
                continue

            merged[cat][idx] = new_record
            index[fp] = (cat, idx, new_record)
            refreshed += 1

    logger.info(
        "Merge discovery: +%s nowych, %s odświeżonych, %s już wyeksportowanych, %s zaawansowanych (bez zmian)",
        added,
        refreshed,
        skipped_processed,
        skipped_advanced,
    )
    return merged


def iter_records_at_stage(
    sheets: dict[str, list[Record]],
    data_sheet_names: tuple[str, ...],
    stage: str,
) -> list[tuple[str, int, Record]]:
    items: list[tuple[str, int, Record]] = []
    for category_name in data_sheet_names:
        for idx, record in enumerate(sheets.get(category_name, [])):
            if record.pipeline_stage == stage:
                items.append((category_name, idx, record))
    return items


def count_records_at_stage(
    sheets: dict[str, list[Record]],
    data_sheet_names: tuple[str, ...],
    stage: str,
) -> int:
    return len(iter_records_at_stage(sheets, data_sheet_names, stage))


def remove_exported_from_staging(
    sheets: dict[str, list[Record]],
    data_sheet_names: tuple[str, ...],
    exported_fps: set[str],
    fingerprint_fn: Callable[[Record], str],
) -> dict[str, list[Record]]:
    cleaned: dict[str, list[Record]] = {}
    for category_name in data_sheet_names:
        cleaned[category_name] = [
            record
            for record in sheets.get(category_name, [])
            if fingerprint_fn(record) not in exported_fps
        ]
    return cleaned
