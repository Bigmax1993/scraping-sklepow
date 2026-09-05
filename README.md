# Automatyczny scraping sklepów

Scraper pobierający dane o planowanych otwarciach sklepów, restauracji, drogerii i centrów handlowych.
Pipeline produkcyjny jest podzielony na **5 segmentów** uruchamianych osobnymi workflow GitHub Actions. Wspólny stan między runami trzyma plik `neueroeffnung_staging.json` oraz cache (GHA Actions Cache).

---


## Pipeline segmentowy (produkcja)

```
DISCOVERY → VALIDATE → MAPS → CONTACT → FINALIZE
 (scrape)   (retry)   (GMaps) (Serper)  (Claude + Excel + mail)
```

| Segment | Co robi | Cron (PL, CEST) | Limit czasu |
|---------|---------|-----------------|-------------|
| **Discovery** | Scrape 4 kategorii, filtr dat, merge do staging | Pon–Pt **03:30** | 4 h |
| **Validate** | Walidacja + retry brakujących pól | Codziennie **08:30** | 2 h |
| **Maps** | Google Maps (Playwright), partia rekordów | Pon–Pt **22:00** | 4 h |
| **Contact** | Serper batch + scrape stron | Sob **02:00** i **18:00** | 4 h × 2 |
| **Finalize** | Claude filtr + Excel + e-mail | Nd **12:00** | 4 h |

### Przepływ tygodnia

```
Pn–Pt 03:30   DISCOVERY
Pn–Pt 08:30   VALIDATE
Pn–Pt 22:00   MAPS        (max 200 rek./run)
Sob 02:00     CONTACT #1
Sob 08:30     VALIDATE
Sob 18:00     CONTACT #2
Nd 08:30      VALIDATE
Nd 12:00      FINALIZE    → Excel + mail
```

### Limit czasu — graceful stop

Gdy segment przekroczy `MAX_RUNTIME_SECONDS`, **zatrzymuje się bez błędu** (GHA = success):

- zapisuje postęp do `neueroeffnung_staging.json` i cache,
- kolejny cron wznawia od miejsca przerwania.

Błąd pojedynczego rekordu (np. Maps) **nie przerywa** całego runu.

---

## GitHub Actions

Repozytorium: [github.com/Bigmax1993/scraping-sklepow](https://github.com/Bigmax1993/scraping-sklepow)

| Workflow | Plik | Trigger |
|----------|------|---------|
| Pipeline — Discovery | `pipeline-discovery.yml` | cron + ręcznie |
| Pipeline — Validate | `pipeline-validate.yml` | cron + ręcznie |
| Pipeline — Maps | `pipeline-maps.yml` | cron + ręcznie |
| Pipeline — Contact | `pipeline-contact.yml` | cron + ręcznie |
| Pipeline — Finalize | `pipeline-finalize.yml` | cron + ręcznie |
| Run scraper (full) | `run-scraper.yml` | **tylko ręcznie** (monolit dev) |

Ręczne uruchomienie: **Actions** → wybierz workflow → **Run workflow**.

### Jednorazowy start: 2026-09-03

Do **2026-09-03 (przed 03:30)** crony segmentów są **wstrzymane** (bramka daty).  
**Pierwszy automatyczny run:** **czwartek 2026-09-03 o 03:30** — **Discovery**, potem harmonogram tygodnia.

| Data | Co się dzieje |
|------|----------------|
| **2026-09-03 (czw)** | 03:30 Discovery → 08:30 Validate → 22:00 Maps |
| **2026-09-04–05** | Discovery, Validate, Maps (pn–pt) |
| **2026-09-06 (sob)** | Contact 02:00 + 18:00, Validate 08:30 |
| **2026-09-07 (nd)** | Validate 08:30 → **Finalize 12:00** (pierwszy Excel + mail) |

Bramka: `.github/scripts/pipeline_start_gate.sh` (`PIPELINE_START_DATE=2026-09-03`). Ręczne **Run workflow** omija bramkę.

### Secrets (GitHub)

| Secret | Używane w |
|--------|-----------|
| `GMAIL_USER` | Finalize, Run scraper (full) |
| `GMAIL_APP_PASSWORD` | Finalize, Run scraper (full) |
| `ANTHROPIC_API_KEY` | Finalize |
| `SERPER_API_KEY` | Contact, Run scraper (full) |

Artifacty: staging, logi, Excel (Finalize) — dostępne po każdym runie w zakładce runu.

Szczegóły techniczne: [docs/ARCHITEKTURA.md](docs/ARCHITEKTURA.md).

---

## Pliki wynikowe i stanu

### Po Finalize (tygodniowy deliverable)

| Plik | Opis |
|------|------|
| `neueroeffnung_wynik.xlsx` | Główny raport Excel (8 arkuszy) |
| `neueroeffnung_wynik.json` | Pełne dane po Claude i filtrach |
| `neueroeffnung_raport_brakow.json` | Rekordy z brakującymi polami |
| `neueroeffnung_scraper.log` | Log bieżącego runu |

### Stan pipeline'u (między segmentami)

| Plik | Opis |
|------|------|
| `neueroeffnung_staging.json` | Rekordy w trakcie pipeline'u (`pipeline_stage` per rekord) |
| `neueroeffnung_processed.json` | Rejestr już wyeksportowanych rekordów |
| `neueroeffnung_detail_cache.json` | Cache stron szczegółów |
| `neueroeffnung_maps_cache.json` | Cache Google Maps |
| `neueroeffnung_contact_cache.json` | Cache kontaktów |
| `neueroeffnung_contact_batch.json` | Raport batch Serper + scrape |
| `neueroeffnung_claude_records.json` | Raport filtra Claude |

Pliki wynikowe Excel/JSON są **nadpisywane** przy każdym Finalize. Staging i cache **rosną** między runami segmentów.

---

## Etapy rekordu (`pipeline_stage`)

```
discovery → validated → po_maps → po_scrape_kontakt → [finalize] → processed
```

| Stage | Znaczenie |
|-------|-----------|
| `discovery` | Świeżo zescrapowany, czeka na walidację |
| `validated` | Po walidacji, gotowy do Maps |
| `po_maps` | Po weryfikacji Google Maps |
| `po_scrape_kontakt` | Po Serper/scrape (lub bez potrzeby kontaktu) |
| *(processed)* | Wyeksportowany — w `neueroeffnung_processed.json`, usunięty ze staging |

---

## Plik Excel — 8 arkuszy

### Data sheets (4 kategorie)

| Sheet | Źródło |
|-------|--------|
| **Markets** | `/branche/supermaerkte` |
| **Restaurants** | `/branche/gastronomie` |
| **Drugstores** | `/branche/drogerie` |
| **Shopping centers** | `/branche/einkaufszentrum` |

### Arkusze analityczne (4)

| Sheet | Opis |
|-------|------|
| **Schedule** | Wszystkie sklepy wg daty otwarcia |
| **By region** | Bundesland, kod pocztowy, miasto |
| **Validation report** | Rekordy ze statusem „Needs review” |
| **Skipped** | Odrzucone (data, godziny, Claude, już wyeksportowane) |

### Kolumny arkuszy danych (EN)

| Kolumna | Opis |
|---------|------|
| Company name | Nazwa sklepu / lokalu |
| Address | Pełny adres (max 650 znaków) |
| Closing date | Data zamknięcia przed remontem *(opcjonalna)* |
| Opening date | Planowana data otwarcia |
| Information | Opis z neueroeffnung / Claude (max 5000 znaków) |
| Entry type | `Neueröffnung` lub `Reopening` |
| Validation status | `OK` lub `Needs review` |
| Missing fields | Np. `information`, `address (incomplete)` |
| Phone | Telefon (pusty, jeśli brak zweryfikowanego) |
| Email | E-mail |
| Contact person | Osoba kontaktowa |

---

## Walidacja

### Wymagane pola

| Pole | Warunek |
|------|---------|
| nazwa_firmy | Niepuste |
| adres | Pełny (ulica + PLZ + miasto) |
| data_otwarcia | W zakresie Q3 2026 – Q4 2028 |
| informacja | Niepusty opis ze strony szczegółów |

**Opcjonalne:** data_zamkniecia.

### Przy brakach

1. Status `Needs review` + wpis w **Validation report**
2. Do **2 retry** pobrania strony szczegółów (segment Validate)
3. Rekord może trafić do Excela ze statusem „Needs review”

---

## Filtry twarde

### Daty otwarcia

- **Od:** 01.07.2026 (Q3 2026)
- **Do:** 31.12.2028 (Q4 2028)

Formaty: `03.09.2026`, `2. Halbjahr 2026`, `4. Quartal 2028`, `2027`, nazwy miesięcy itd.

### Godziny pracy

Rekordy z godzinami pracy (Maps lub wzorzec w tekście) → tylko **Skipped**, nie Excel.

### Claude (Finalize)

Filtr jakości + jeden spójny opis (`informacja`) + weryfikacja kontaktów. Odrzucone → **Skipped** (`Rejected by Claude record filter`).

### Kontakty

Tylko dane z `verified=true` (Claude) trafiają do JSON/Excel. Reszta zostaje w `neueroeffnung_contact_batch.json`.

---

## Pomijanie już wyeksportowanych

Przy Discovery scraper pomija rekordy z `neueroeffnung_processed.json` (klucz: nazwa + adres + data otwarcia). Po Finalize nowe rekordy trafiają do rejestru i są usuwane ze staging.

---

## Uzupełnianie danych

1. **neueroeffnung.info** — listy + strony szczegółów; brak adresu → `/suche/`
2. **Google Maps** (segment Maps) — adres, `godziny_pracy`, `maps_zweryfikowany`
3. **Serper + scrape** (segment Contact) — strony WWW, dane kontaktowe surowe
4. **Claude** (segment Finalize) — filtr, opis, weryfikacja kontaktów

---

## Zmienne środowiskowe

### Segmentacja

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `PIPELINE_STAGE` | `full` | `discovery`, `validate`, `maps`, `contact`, `finalize`, `full` |
| `MAX_RUNTIME_SECONDS` | `14400` | Limit czasu segmentu (Validate: `7200` w GHA) |
| `MAPS_BATCH_LIMIT` | `200` | Max rekordów Maps na run |
| `CONTACT_BATCH_LIMIT` | `250` | Max jobów Contact na run |

### Enrichment

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `ENABLE_GOOGLE_MAPS_ENRICHMENT` | `true` | Warstwa Google Maps |
| `GOOGLE_MAPS_HEADLESS` | `true` | Playwright w tle |
| `ENABLE_CONTACT_ENRICHMENT` | `true` | Serper + scrape |
| `ENABLE_CLAUDE_RECORD_NORMALIZE` | `true` | Filtr + opis + kontakty (Finalize) |
| `ANTHROPIC_API_KEY` | — | Klucz Anthropic |
| `SERPER_API_KEY` | — | Klucz Serper |
| `CLAUDE_CONTACT_MODEL` | `claude-sonnet-4-6` | Model Claude |
| `CONTACT_CLAUDE_BATCH_SIZE` | `20` | Rekordów na wywołanie Claude |

### E-mail

| Zmienna | Opis |
|---------|------|
| `GMAIL_USER` | Konto Gmail nadawcy |
| `GMAIL_APP_PASSWORD` | Hasło aplikacji Gmail |
| `SEND_EMAIL` | `false` — wyłącza wysyłkę |

Odbiorca: **svinchak1993@gmail.com** (stały w `send_mail.py`).

Ręczna wysyłka: `python send_mail.py`

---

## Konfiguracja (stałe w kodzie)

| Parametr | Domyślnie | Opis |
|----------|-----------|------|
| `MAX_PAGES_PER_CATEGORY` | 30 | Max stron listy / kategoria |
| `REQUEST_DELAY_SEC` | 0.8 | Opóźnienie HTTP (s) |
| `MAX_ADDRESS_LENGTH` | 650 | Max długość adresu |
| `MAX_INFO_LENGTH` | 5000 | Max długość opisu |
| `MAX_VALIDATION_RETRIES` | 2 | Retry przy brakach |

---

## Testy

```powershell
python -m pytest tests/ -v
```

**146 testów** — unit, integration, regression.

```powershell
python -m pytest tests/ -m unit -v
python -m pytest tests/unit/test_pipeline_state.py -v
```

W CI testy z wyłączonym Maps/Contact/Claude uruchamiane tylko w `run-scraper.yml` (full).

---

## Struktura projektu

```
Automatyczny scraping sklepow/
├── neueroeffnung_scraper.py      # główny skrypt + etapy PIPELINE_STAGE
├── pipeline_state.py             # staging, merge, RuntimeBudget
├── google_maps_enricher.py       # Playwright + Maps cache
├── contact_enrichment.py         # Serper + scrape
├── claude_record_normalizer.py   # filtr Claude + spójny JSON
├── send_mail.py                  # wysyłka Excela (Gmail)
├── .github/workflows/
│   ├── pipeline-discovery.yml
│   ├── pipeline-validate.yml
│   ├── pipeline-maps.yml
│   ├── pipeline-contact.yml
│   ├── pipeline-finalize.yml
│   └── run-scraper.yml           # monolit (ręcznie)
├── docs/
│   └── ARCHITEKTURA.md
├── tests/
└── requirements.txt
```

---

## Rozwiązywanie problemów

| Problem | Rozwiązanie |
|---------|-------------|
| Segment „nic nie robi” | Sprawdź `pipeline_stage` w staging — może brak rekordów na tym etapie |
| Maps/Contact urwane w połowie | Normalne przy limicie czasu — następny cron wznawia |
| Pusty Excel w tygodniu | Finalize tylko nd. 12:00; sprawdź czy rekordy doszły do `po_scrape_kontakt` |
| Status „Needs review” | Arkusz **Validation report** lub `neueroeffnung_raport_brakow.json` |
| Brak kontaktów | Sprawdź `SERPER_API_KEY`, `neueroeffnung_contact_batch.json` |
| Stary cache psuje dane | Usuń odpowiedni `*_cache.json` i uruchom segment ponownie |
| GHA cache „puste” | Pierwszy run segmentu startuje od zera; kolejne runy łączą stan |

---

## Licencja i uwagi

Dane pochodzą z publicznego serwisu neueroeffnung.info. Używaj scrapera z poszanowaniem regulaminu serwisu i rozsądnym opóźnieniem między żądaniami.
