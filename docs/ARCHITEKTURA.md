# Architektura scrapera neueroeffnung.info

## Przegląd

Cała logika w jednym pliku: `neueroeffnung_scraper.py`.

Pipeline: **Scraping → Czyszczenie → JSON → Walidacja (+ retry) → Raport braków → Excel**

---

## Typy danych

```python
@dataclass
class ListingItem:          # wpis ze strony listy
    nazwa: str
    data_otwarcia: str
    adres_lista: str
    url: str = ""
    entry_type: str = ""    # "reopening" | "new_opening"

@dataclass
class Record:               # wiersz w Excelu / JSON
    nazwa_firmy: str
    adres: str
    data_zamkniecia: str
    data_otwarcia: str
    informacja: str = ""
    typ_wpisu: str = ""             # Neueröffnung / Reopening
    kategoria: str = ""             # nazwa arkusza
    detail_url: str = ""            # URL strony szczegółów (do retry)
    listing_adres_lista: str = ""   # oryginalny adres z listy
    entry_type_raw: str = ""        # surowy entry_type
    status_walidacji: str = "OK"    # OK | Wymaga weryfikacji
    brakujace_pola: str = ""        # np. "informacja, adres (niepełny)"
    proby_ponowienia: int = 0

@dataclass
class SkippedRecord:        # arkusz „Pominięte”
    kategoria: str
    nazwa_firmy: str
    adres: str
    data_otwarcia: str
    typ_wpisu: str
    powod: str
```

---

## Przepływ `run_scraper()`

```
1. collect_category_records()  × CATEGORIES (4 kategorie)
2. clean_all_sheets()
3. save_json_dataset(stage="po_scrapingu")
4. run_validation_pipeline()
   ├── validate_all_records()
   ├── retry_record() × MAX_VALIDATION_RETRIES
   └── validate_all_records() (ponownie)
5. save_json_dataset(stage="po_walidacji")
6. save_validation_report_json()
7. collect_verification_skipped()
8. write_excel()  → 8 arkuszy
9. send_mail.send_excel()  → Gmail (svinchak1993@gmail.com)
```

---

## Scraping

### Parsowanie listy

`iter_category_pages()` → `parse_list_page()`

| Format HTML | Selektor | Typowe dla |
|-------------|----------|------------|
| Karty z linkiem | `a.entry-card` | Strona 1 |
| Karty bez linku | `div.col-md-3.mb-4 > .card` | Strona 2+ |

Paginacja: `<link rel="next">` w `<head>`.

### Budowa rekordu — `listing_to_record()`

1. Brak URL → `resolve_detail_url()` przez `/suche/`
2. `fetch_detail()` ze strony szczegółów:
   - `extract_address()` — sekcja Kontaktdaten
   - `extract_opening_date()` — nagłówek „Eröffnung:"
   - `extract_information()` — akapity w `div.text-wrap-break-word`
   - `extract_closing_date()` — regex w opisie
3. `validate_address()` (max 650 znaków)
4. `validate_information()` (max 5000 znaków)

### Filtrowanie — `collect_category_records()`

- Deduplikacja: `(nazwa, adres_lista, data_otwarcia)`
- Daty: `is_opening_date_in_range()` — Q3 2026 – Q4 2028
- Odrzucone daty → `SkippedRecord` (arkusz Pominięte)

---

## Kategorie i źródła

```python
CATEGORIES = {
    "Markety":           "/branche/supermaerkte",
    "Restauracje":       "/branche/gastronomie",
    "Drogerie":          "/branche/drogerie",
    "Centra handlowe":   "/branche/einkaufszentrum",
}
```

---

## Walidacja

### `find_missing_fields(record) → list[str]`

| Pole | Warunek braku |
|------|---------------|
| `nazwa_firmy` | Puste |
| `adres` | Puste lub `is_incomplete_address()` |
| `data_otwarcia` | Puste lub poza zakresem |
| `informacja` | Puste |

### `run_validation_pipeline()`

1. `validate_all_records()` — ustawia `status_walidacji` i `brakujace_pola`
2. Dla każdego rekordu z brakami → `retry_record()`:
   - `invalidate_record_cache()` — usuwa wpis z cache URL i wyszukiwania
   - `listing_to_record()` — ponowne pobranie
   - `proby_ponowienia += 1`
3. Powtarza do `MAX_VALIDATION_RETRIES` (domyślnie 2)
4. Zwraca `validation_summary`: `{ok, wymaga_weryfikacji, total}`

---

## JSON

### `neueroeffnung_wynik.json`

```json
{
  "generated_at": "2026-09-02T12:00:00",
  "stage": "po_walidacji",
  "opening_filter": {"start": "2026-07-01", "end": "2028-12-31"},
  "validation_summary": {"ok": 120, "wymaga_weryfikacji": 5, "total": 125},
  "sheets": {
    "Markety": [{ "nazwa_firmy": "...", "status_walidacji": "OK", ... }],
    ...
  }
}
```

### `neueroeffnung_raport_brakow.json`

Tylko rekordy ze statusem `Wymaga weryfikacji`.

---

## Excel — `write_excel()`

| # | Arkusz | Kolumny |
|---|--------|---------|
| 1–4 | Markety … Centra handlowe | `EXCEL_COLUMNS` (8 kolumn) |
| 5 | Harmonogram | `HARMONOGRAM_COLUMNS` |
| 6 | Według regionu | `REGION_COLUMNS` |
| 7 | Raport braków | `VALIDATION_REPORT_COLUMNS` |
| 8 | Pominięte | `SKIPPED_COLUMNS` |

### Region — `plz_to_bundesland()`

Mapowanie 2-cyfrowego prefiksu PLZ na Bundesland (słownik `PLZ_BUNDESLAND`, 01–99).

---

## Wyszukiwanie adresu

`resolve_detail_url()` → scoring (`score_search_match`):

| Kryterium | Punkty |
|-----------|--------|
| Identyczna nazwa | +20 |
| Nazwa zawiera się w tytule | +10 |
| PLZ w wyniku | +15 |
| Miasto w wyniku | +8 |
| Pełny adres_lista w lokalizacji | +12 |

Akceptacja: score ≥ 15.

---

## Cache

### Strony szczegółów — `neueroeffnung_detail_cache.json`

```json
{
  "https://www.neueroeffnung.info/.../rewe-esch": {
    "adres": "...",
    "data_otwarcia": "...",
    "data_zamkniecia": "...",
    "informacja": "..."
  },
  "search::REWE Esch::41189 Mönchengladbach": {
    "url": "https://..."
  }
}
```

Przy retry walidacji odpowiednie klucze są usuwane (`invalidate_record_cache`).

---

## Czyszczenie danych

`clean_all_sheets()` → `clean_records()` → `clean_record()`:

- `clean_text_local()` — HTML entities, mojibake, białe znaki
- `validate_address()` / `validate_information()` — limity długości
- Pola metadanych (`typ_wpisu`, `kategoria`, `status_walidacji`…) przechodzą bez zmian

---

## Testy (91)

| Warstwa | Pliki | Zakres |
|---------|-------|--------|
| Unit | `test_parsers.py` | Parsowanie HTML, harmonogram, region |
| Unit | `test_opening_date_filter.py` | Filtr dat Q3 2026 – Q4 2028 |
| Unit | `test_address_validation.py` | Adresy, `/suche/` |
| Unit | `test_validation.py` | JSON, walidacja, retry, raport braków |
| Unit | `test_text_cleaning.py` | Lokalne czyszczenie tekstu |
| Unit | `test_send_mail.py` | Gmail SMTP (mock) |
| Integration | `test_scraper_flow.py` | Mock HTTP, Excel E2E, e-mail |
| Regression | `test_regression.py` | Golden files |

---

## Rozszerzanie

### Nowa kategoria

1. Dodaj URL do `CATEGORIES` w skrypcie
2. Dodaj nazwę do `DATA_SHEET_NAMES`
3. Uruchom scraper — arkusz powstanie automatycznie

### Zmiana zakresu dat

Edytuj `OPENING_FILTER_START` i `OPENING_FILTER_END`.

### Zmiana liczby ponowień walidacji

Edytuj `MAX_VALIDATION_RETRIES`.
