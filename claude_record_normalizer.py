"""
Claude — filtrowanie rekordów i generowanie spójnego wpisu JSON.

Dla każdego rekordu (batch):
- accept / reject (filtr jakości)
- jedno spójne pole informacja (bez UPDATE-ów, bez godzin pracy)
- weryfikacja kontaktów (tylko verified=true trafia do JSON)
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from contact_enrichment import (
    CLAUDE_BATCH_CHUNK_SIZE,
    ContactData,
    _parse_claude_json,
    apply_contact_to_record,
    contact_cache_key,
)

SCRIPT_DIR = Path(__file__).resolve().parent
CLAUDE_RECORD_REPORT_FILE = SCRIPT_DIR / "neueroeffnung_claude_records.json"
MAX_INFORMATION_FOR_CLAUDE = 5000


@dataclass
class RecordNormalizationJob:
    job_id: str
    category: str
    record_index: int
    company_name: str
    address: str
    opening_date: str
    closing_date: str
    entry_type: str
    information_raw: str
    maps_verified: bool = False
    working_hours_maps: str = ""
    telefon: str = ""
    email: str = ""
    website: str = ""
    osoba_kontaktowa: str = ""
    scraped_contacts: dict = field(default_factory=dict)
    accept: bool = True
    reject_reason: str = ""
    informacja: str = ""
    data_otwarcia: str = ""
    data_zamkniecia: str = ""
    contacts_verified: bool = False
    verified_contact: ContactData = field(default_factory=ContactData)


def is_record_normalization_enabled() -> bool:
    flag = os.environ.get("ENABLE_CLAUDE_RECORD_NORMALIZE", "true").lower()
    if flag not in ("1", "true", "yes"):
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def save_claude_record_report(report: dict, logger: logging.Logger) -> None:
    try:
        with open(CLAUDE_RECORD_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info("Zapisano raport Claude rekordów: %s", CLAUDE_RECORD_REPORT_FILE.resolve())
    except Exception as exc:
        logger.error("Błąd zapisu raportu Claude rekordów: %s", exc)


def _scrape_map_from_contact_report(contact_report: dict | None) -> dict[str, dict]:
    if not contact_report:
        return {}
    mapping: dict[str, dict] = {}
    for job in contact_report.get("jobs", []):
        key = contact_cache_key(job.get("company_name", ""), job.get("address", ""))
        scraped = job.get("scraped") or {}
        if scraped:
            mapping[key] = scraped
    return mapping


def build_record_normalization_jobs(
    sheets: dict[str, list[Any]],
    data_sheet_names: tuple[str, ...],
    contact_report: dict | None,
) -> list[RecordNormalizationJob]:
    scrape_map = _scrape_map_from_contact_report(contact_report)
    jobs: list[RecordNormalizationJob] = []
    counter = 0
    for category_name in data_sheet_names:
        for idx, record in enumerate(sheets.get(category_name, [])):
            counter += 1
            key = contact_cache_key(record.nazwa_firmy, record.adres)
            jobs.append(
                RecordNormalizationJob(
                    job_id=str(counter),
                    category=category_name,
                    record_index=idx,
                    company_name=record.nazwa_firmy,
                    address=record.adres,
                    opening_date=record.data_otwarcia,
                    closing_date=record.data_zamkniecia,
                    entry_type=record.typ_wpisu,
                    information_raw=(record.informacja or "")[:MAX_INFORMATION_FOR_CLAUDE],
                    maps_verified=bool(record.maps_zweryfikowany),
                    working_hours_maps=record.godziny_pracy or "",
                    telefon=record.telefon,
                    email=record.email,
                    website=record.website,
                    osoba_kontaktowa=record.osoba_kontaktowa,
                    scraped_contacts=scrape_map.get(key, {}),
                )
            )
    return jobs


def batch_normalize_records_with_claude(
    jobs: list[RecordNormalizationJob],
    logger: logging.Logger,
) -> None:
    if not jobs:
        return
    if not is_record_normalization_enabled():
        for job in jobs:
            job.accept = True
            job.informacja = job.information_raw
            job.data_otwarcia = job.opening_date
            job.data_zamkniecia = job.closing_date
        return

    try:
        import anthropic
    except ImportError:
        logger.warning("Brak pakietu anthropic — pomijam normalizację rekordów Claude")
        for job in jobs:
            job.accept = True
            job.informacja = job.information_raw
        return

    client = anthropic.Anthropic()
    model = os.environ.get("CLAUDE_CONTACT_MODEL", "claude-sonnet-4-6")
    chunk_size = int(os.environ.get("CONTACT_CLAUDE_BATCH_SIZE", str(CLAUDE_BATCH_CHUNK_SIZE)))

    for chunk_start in range(0, len(jobs), chunk_size):
        chunk = jobs[chunk_start : chunk_start + chunk_size]
        payload = []
        for job in chunk:
            payload.append(
                {
                    "id": job.job_id,
                    "company_name": job.company_name,
                    "address": job.address,
                    "opening_date": job.opening_date,
                    "closing_date": job.closing_date,
                    "entry_type": job.entry_type,
                    "information_raw": job.information_raw,
                    "maps_verified": job.maps_verified,
                    "working_hours_maps": job.working_hours_maps,
                    "existing_contacts": {
                        "telefon": job.telefon,
                        "email": job.email,
                        "website": job.website,
                        "osoba_kontaktowa": job.osoba_kontaktowa,
                    },
                    "scraped_contacts": job.scraped_contacts,
                }
            )

        prompt = f"""Jesteś filtrem jakości i redaktorem danych o planowanych otwarciach sklepów w Niemczech/Austrii.

Dla KAŻDEGO rekordu:
1. Oceń, czy to prawdziwe, sensowne otwarcie / reopening handlu (retail, gastronomia, drogeria, centrum handlowe).
2. Jeśli wpis jest śmieciowy, duplikat bez sensu, zła lokalizacja, nie handel — accept=false i krótki reject_reason (PL).
3. Jeśli accept=true — wygeneruj JEDEN spójny opis w języku niemieckim (informacja):
   - bez bloków UPDATE #1/#2/#3,
   - bez godzin pracy (Mo-Sa, 08:00 itd.),
   - 2–4 zdania, tylko fakty: co, gdzie, kiedy, kontekst (remont/nowy obiekt).
4. Potwierdź lub skoryguj data_otwarcia / data_zamkniecia (format jak wejście, np. 03.09.2026).
5. Zweryfikuj kontakty (existing + scraped). contacts_verified=true tylko przy wysokiej pewności.
   Przy contacts_verified=false — puste pola telefon/email/website/osoba_kontaktowa.

Zwróć WYŁĄCZNIE JSON:
{{"records":[{{"id":"","accept":true/false,"reject_reason":"","informacja":"","data_otwarcia":"","data_zamkniecia":"","telefon":"","email":"","website":"","osoba_kontaktowa":"","contacts_verified":true/false}}]}}

Rekordy:
{json.dumps(payload, ensure_ascii=False)}
"""
        try:
            message = client.messages.create(
                model=model,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )
            result = _parse_claude_json(message.content[0].text)
            by_id = {item["id"]: item for item in result.get("records", []) if item.get("id")}
            for job in chunk:
                item = by_id.get(job.job_id, {})
                job.accept = bool(item.get("accept", False))
                job.reject_reason = (item.get("reject_reason") or "").strip()
                job.informacja = (item.get("informacja") or "").strip()
                job.data_otwarcia = (item.get("data_otwarcia") or job.opening_date).strip()
                job.data_zamkniecia = (item.get("data_zamkniecia") or job.closing_date).strip()
                job.contacts_verified = bool(item.get("contacts_verified"))
                job.verified_contact = ContactData(
                    telefon=(item.get("telefon") or "").strip(),
                    email=(item.get("email") or "").strip(),
                    website=(item.get("website") or "").strip(),
                    osoba_kontaktowa=(item.get("osoba_kontaktowa") or "").strip(),
                    verified=job.contacts_verified,
                )
                logger.info(
                    "  Claude rekord [%s]: accept=%s | info=%s znaków | kontakt=%s",
                    job.company_name,
                    job.accept,
                    len(job.informacja),
                    "verified" if job.contacts_verified else "brak",
                )
        except Exception as exc:
            logger.warning("Claude normalizacja błąd (chunk %s): %s", chunk_start, exc)
            for job in chunk:
                job.accept = True
                job.informacja = job.information_raw
                job.data_otwarcia = job.opening_date
                job.data_zamkniecia = job.closing_date


def apply_normalization_job_to_record(record: Any, job: RecordNormalizationJob) -> None:
    record.claude_zweryfikowany = bool(job.accept)
    if not job.accept:
        return
    if job.informacja:
        record.informacja = job.informacja[:5000]
    if job.data_otwarcia:
        record.data_otwarcia = job.data_otwarcia
    if job.data_zamkniecia:
        record.data_zamkniecia = job.data_zamkniecia
    contact = job.verified_contact
    contact.verified = job.contacts_verified
    apply_contact_to_record(record, contact)


def job_to_report_entry(job: RecordNormalizationJob) -> dict:
    return {
        "job_id": job.job_id,
        "category": job.category,
        "company_name": job.company_name,
        "address": job.address,
        "accept": job.accept,
        "reject_reason": job.reject_reason,
        "information_raw_len": len(job.information_raw),
        "informacja": job.informacja,
        "data_otwarcia": job.data_otwarcia,
        "data_zamkniecia": job.data_zamkniecia,
        "contacts_verified": job.contacts_verified,
        "verified_contact": asdict(job.verified_contact),
    }


def run_claude_record_normalization(
    sheets: dict[str, list[Any]],
    data_sheet_names: tuple[str, ...],
    skipped: list[Any],
    skip_reason: str,
    skipped_record_cls: type,
    logger: logging.Logger,
    contact_report: dict | None = None,
) -> tuple[dict[str, list[Any]], dict]:
    jobs = build_record_normalization_jobs(sheets, data_sheet_names, contact_report)
    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "enabled": is_record_normalization_enabled(),
        "jobs_total": len(jobs),
        "accepted": 0,
        "rejected": 0,
        "jobs": [],
    }

    if not jobs:
        save_claude_record_report(report, logger)
        return sheets, report

    batch_normalize_records_with_claude(jobs, logger)

    rejected = 0
    accepted = 0
    for job in jobs:
        record = sheets[job.category][job.record_index]
        if not job.accept:
            rejected += 1
            logger.info(
                "  Odrzucono (Claude): %s | %s",
                record.nazwa_firmy,
                job.reject_reason or "quality filter",
            )
            skipped.append(
                skipped_record_cls(
                    kategoria=job.category,
                    nazwa_firmy=record.nazwa_firmy,
                    adres=record.adres,
                    data_otwarcia=record.data_otwarcia,
                    typ_wpisu=record.typ_wpisu,
                    powod=job.reject_reason or skip_reason,
                )
            )
            continue
        accepted += 1
        apply_normalization_job_to_record(record, job)
        if job.contacts_verified and job.verified_contact.has_any():
            from contact_enrichment import contact_cache_key, load_contact_cache, save_contact_cache

            cache = load_contact_cache(logger)
            key = contact_cache_key(job.company_name, job.address)
            cache[key] = {
                **asdict(job.verified_contact),
                "verified": True,
                "source_url": job.verified_contact.source_url or record.kontakt_zrodlo,
            }
            save_contact_cache(cache, logger)
        report["jobs"].append(job_to_report_entry(job))

    for category_name in data_sheet_names:
        sheets[category_name] = [
            sheets[category_name][job.record_index]
            for job in jobs
            if job.category == category_name and job.accept
        ]

    report["accepted"] = accepted
    report["rejected"] = rejected
    save_claude_record_report(report, logger)
    logger.info(
        "Claude rekordy: %s zaakceptowanych, %s odrzuconych",
        accepted,
        rejected,
    )
    return sheets, report
