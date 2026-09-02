"""Testy stanu pipeline'u segmentowego."""

from __future__ import annotations

import time

import neueroeffnung_scraper as scraper
from pipeline_state import (
    STAGE_DISCOVERY,
    STAGE_PO_MAPS,
    RuntimeBudget,
    empty_sheets,
    merge_discovery_sheets,
)


def _record(name: str, stage: str = STAGE_DISCOVERY) -> scraper.Record:
    return scraper.Record(
        nazwa_firmy=name,
        adres="Teststraße 1, 12345 Berlin",
        data_zamkniecia="",
        data_otwarcia="03.09.2026",
        pipeline_stage=stage,
    )


class TestMergeDiscovery:
    def test_adds_new_records(self, silent_logger):
        staging = empty_sheets(scraper.DATA_SHEET_NAMES)
        discovered = {"Markets": [_record("Lidl")], **{n: [] for n in scraper.DATA_SHEET_NAMES if n != "Markets"}}
        merged = merge_discovery_sheets(
            staging,
            discovered,
            scraper.DATA_SHEET_NAMES,
            scraper.record_fingerprint,
            set(),
            silent_logger,
        )
        assert len(merged["Markets"]) == 1
        assert merged["Markets"][0].pipeline_stage == STAGE_DISCOVERY

    def test_does_not_overwrite_advanced_stage(self, silent_logger):
        existing = _record("Lidl", stage=STAGE_PO_MAPS)
        staging = {name: [] for name in scraper.DATA_SHEET_NAMES}
        staging["Markets"] = [existing]
        discovered = {"Markets": [_record("Lidl")], **{n: [] for n in scraper.DATA_SHEET_NAMES if n != "Markets"}}
        merged = merge_discovery_sheets(
            staging,
            discovered,
            scraper.DATA_SHEET_NAMES,
            scraper.record_fingerprint,
            set(),
            silent_logger,
        )
        assert merged["Markets"][0].pipeline_stage == STAGE_PO_MAPS


class TestRuntimeBudget:
    def test_not_expired_at_start(self, silent_logger):
        budget = RuntimeBudget(max_seconds=3600)
        assert budget.check(silent_logger) is True
        assert budget.expired() is False
        assert budget.time_limit_hit is False

    def test_check_sets_flag_without_exception(self, silent_logger, monkeypatch):
        budget = RuntimeBudget(max_seconds=1)
        start = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: start + 2)
        assert budget.check(silent_logger) is False
        assert budget.time_limit_hit is True
