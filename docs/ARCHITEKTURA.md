# Architektura pipeline'u

Pipeline podzielony na **5 segmentów** ze wspólnym stanem `neueroeffnung_staging.json` i cache między runami GHA.

## Harmonogram (czas polski, CEST UTC+2)

| Segment | Kiedy | Timeout | Workflow |
|---------|-------|---------|----------|
| **Discovery** | Pon–Pt **03:30** | 4 h | `pipeline-discovery.yml` |
| **Validate** | Codziennie **08:30** | 2 h | `pipeline-validate.yml` |
| **Maps** | Pon–Pt **22:00** | 4 h | `pipeline-maps.yml` |
| **Contact** | Sobota **02:00** i **18:00** | 4 h × 2 | `pipeline-contact.yml` |
| **Finalize** | Niedziela **12:00** | 4 h | `pipeline-finalize.yml` |

Pełny monolit (testy / ręcznie): `run-scraper.yml` → `PIPELINE_STAGE=full`

## Przepływ tygodnia

```
Pn–Pt 03:30   DISCOVERY  (scrape → staging)
Pn–Pt 08:30   VALIDATE   (walidacja rekordów discovery)
Pn–Pt 22:00   MAPS       (partia max 200, limit 4 h)
Sob 02:00     CONTACT run 1
Sob 08:30     VALIDATE
Sob 18:00     CONTACT run 2
Nd 08:30      VALIDATE
Nd 12:00      FINALIZE   (Claude + Excel + mail)
```

## Etapy rekordu (`pipeline_stage`)

```
discovery → validated → po_maps → po_scrape_kontakt → [finalize] → processed
```

## Uruchomienie lokalne

```bash
set PIPELINE_STAGE=discovery
python neueroeffnung_scraper.py
```

Dozwolone wartości: `discovery`, `validate`, `maps`, `contact`, `finalize`, `full`.

## Zmienne środowiskowe

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `PIPELINE_STAGE` | `full` | Aktywny segment |
| `MAX_RUNTIME_SECONDS` | `14400` (4 h) | Twardy limit czasu runu |
| `MAPS_BATCH_LIMIT` | `200` | Max rekordów Maps na run |
| `CONTACT_BATCH_LIMIT` | `250` | Max jobów contact na run |

Limit czasu **nie powoduje błędu joba GHA** — segment zapisuje postęp i kończy się statusem success (exit 0). Kolejny cron wznawia od zapisanego stagingu.

## Pliki stanu (GHA cache)

- `neueroeffnung_staging.json` — rekordy w trakcie pipeline'u
- `neueroeffnung_detail_cache.json` — cache stron szczegółów
- `neueroeffnung_maps_cache.json` — cache Google Maps
- `neueroeffnung_contact_cache.json` — cache kontaktów
- `neueroeffnung_processed.json` — już wyeksportowane rekordy
