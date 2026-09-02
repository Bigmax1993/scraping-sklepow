# Automatyczny scraping sklepów — neueroeffnung.info

Scraper pobierający dane o planowanych otwarciach sklepów, restauracji, drogerii i centrów handlowych z serwisu [neueroeffnung.info](https://www.neueroeffnung.info). Dane przechodzą przez pipeline **Scraping → JSON → Walidacja → Excel → E-mail**.

## Szybki start

```powershell
cd "C:\Users\svinc\Documents\Automatyczny scraping sklepow"

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python neueroeffnung_scraper.py
```

### Pliki wynikowe

| Plik | Opis |
|------|------|
| `neueroeffnung_wynik.xlsx` | Główny raport Excel (8 arkuszy) |
| `neueroeffnung_wynik.json` | Pełne dane po scrapingu i po walidacji |
| `neueroeffnung_raport_brakow.json` | Rekordy z brakującymi polami |
| `neueroeffnung_scraper.log` | Log przebiegu (nadpisywany przy każdym uruchomieniu) |

Każde uruchomienie **nadpisuje** pliki wynikowe od zera — dane nie są dopisywane do poprzednich tabel. Cache stron szczegółów (`neueroeffnung_detail_cache.json`) jest zachowywany, żeby przyspieszyć ponowne pobrania.

---

## Pipeline danych

```
🌐 SCRAPING (neueroeffnung.info)
        │
        ▼
  Filtr dat Q3 2026 – Q4 2028
        │
        ▼
  Czyszczenie znaków (lokalnie: HTML entities, błędy kodowania)
        │
        ▼
📦 JSON  →  neueroeffnung_wynik.json  (stage: po_scrapingu)
        │
        ▼
🔍 WALIDACJA  (nazwa, adres, data otwarcia, informacja)
        │
        ├─ wszystko OK ─────────────────────────────┐
        │                                           │
        └─ braki → 🔄 ponów pobranie (max 2×)      │
                      │                             │
                      └─ nadal braki?               │
                           ⚠️ Wymaga weryfikacji    │
                           📊 Raport braków         │
                                                   ▼
📦 JSON  →  neueroeffnung_wynik.json  (stage: po_walidacji)
📊 JSON  →  neueroeffnung_raport_brakow.json
        │
        ▼
📁 EXCEL →  neueroeffnung_wynik.xlsx
        │
        ▼
📧 E-MAIL →  neueroeffnung_wynik.xlsx  →  svinchak1993@gmail.com
```

---

## Plik Excel — 8 arkuszy

### Arkusze danych (4 kategorie)

| Arkusz | Źródło |
|--------|--------|
| **Markety** | `/branche/supermaerkte` |
| **Restauracje** | `/branche/gastronomie` |
| **Drogerie** | `/branche/drogerie` |
| **Centra handlowe** | `/branche/einkaufszentrum` |

### Arkusze analityczne i kontrolne (4)

| Arkusz | Opis |
|--------|------|
| **Harmonogram** | Wszystkie sklepy posortowane chronologicznie po dacie otwarcia (z datą zamknięcia) |
| **Według regionu** | Bundesland, PLZ, miasto + dane sklepu (w tym data zamknięcia) |
| **Raport braków** | Rekordy ze statusem „Wymaga weryfikacji” (w tym data zamknięcia) |
| **Pominięte** | Wpisy odrzucone (data poza zakresem) + oznaczone walidacją |

### Kolumny arkuszy danych

| Kolumna | Opis |
|---------|------|
| Nazwa firmy | Nazwa sklepu / lokalu |
| Adres | Pełny adres (ulica, PLZ, miasto) — max 650 znaków |
| data zamknięcia | Data zamknięcia przed remontem — wyciągana z opisu *(opcjonalna)* |
| data otwarcia | Planowana data otwarcia |
| informacja | Pełny opis ze strony szczegółów — max 5000 znaków |
| Typ wpisu | `Neueröffnung` lub `Reopening` |
| Status walidacji | `OK` lub `Wymaga weryfikacji` |
| Brakujące pola | Np. `informacja`, `adres (niepełny)` |

---

## Walidacja

### Sprawdzane pola (wymagane)

| Pole | Warunek |
|------|---------|
| nazwa_firmy | Niepuste |
| adres | Pełny (ulica + PLZ + miasto, nie samo „80331 München”) |
| data_otwarcia | Niepuste i w zakresie Q3 2026 – Q4 2028 |
| informacja | Niepusty opis ze strony szczegółów |

**Nie jest wymagane:** data zamknięcia (neueroeffnung.info rzadko ją podaje).

### Co robi scraper przy brakach

1. Zapisuje dane do JSON
2. Sprawdza każdy rekord (`find_missing_fields`)
3. Dla rekordów z brakami — **czyści cache** i ponawia pobranie strony szczegółów (domyślnie **2 próby**)
4. Rekordy nadal niekompletne → status `Wymaga weryfikacji` + wpis w **Raport braków**
5. Dopiero potem zapisuje Excel

---

## Filtr dat otwarcia

Do wyników trafiają tylko wpisy z datą otwarcia w zakresie:

- **od:** 01.07.2026 (Q3 2026)
- **do:** 31.12.2028 (Q4 2028)

Obsługiwane formaty: `03.09.2026`, `2. Halbjahr 2026`, `4. Quartal 2028`, `2027`, nazwy miesięcy itd.

---

## Uzupełnianie adresów

- **Strona 1** listy — karty z linkiem → pełne dane od razu ze strony szczegółów
- **Strona 2+** — tylko PLZ + miasto → wyszukiwanie przez `/suche/Nazwa+PLZ+Miasto` → pobranie pełnego adresu i opisu

---

## Pliki pomocnicze (cache)

| Plik | Rola |
|------|------|
| `neueroeffnung_detail_cache.json` | Cache stron szczegółów (adres, daty, informacja) |
| `neueroeffnung_scraper.log` | Szczegółowy log |

Przy problemach z brakującymi danymi usuń cache i uruchom ponownie:

```powershell
Remove-Item neueroeffnung_detail_cache.json -ErrorAction SilentlyContinue
python neueroeffnung_scraper.py
```

---

## Konfiguracja

Główne stałe w `neueroeffnung_scraper.py`:

| Parametr | Domyślnie | Opis |
|----------|-----------|------|
| `MAX_PAGES_PER_CATEGORY` | 30 | Maks. stron listy na kategorię |
| `REQUEST_DELAY_SEC` | 0.8 | Opóźnienie między żądaniami HTTP (s) |
| `MAX_ADDRESS_LENGTH` | 650 | Maks. długość adresu |
| `MAX_INFO_LENGTH` | 5000 | Maks. długość kolumny informacja |
| `MAX_VALIDATION_RETRIES` | 2 | Liczba ponownych pobrań przy brakach |
| `OPENING_FILTER_START` | 2026-07-01 | Początek filtra dat |
| `OPENING_FILTER_END` | 2028-12-31 | Koniec filtra dat |

### Wysyłka e-mail (Gmail)

Po zakończeniu pipeline scraper wysyła `neueroeffnung_wynik.xlsx` na **svinchak1993@gmail.com** (SMTP Gmail + kopia w folderze Wysłane).

| Zmienna | Opis |
|---------|------|
| `GMAIL_USER` | Konto Gmail nadawcy, np. `svinchak1993@gmail.com` |
| `GMAIL_APP_PASSWORD` | Hasło do aplikacji Gmail (16 znaków) |
| `SEND_EMAIL` | `false` — wyłącza wysyłkę po scrapingu |

Odbiorca jest stały: **svinchak1993@gmail.com**.

Zmienne można ustawić trwale w PowerShell:

```powershell
[Environment]::SetEnvironmentVariable("GMAIL_USER", "svinchak1993@gmail.com", "User")
[Environment]::SetEnvironmentVariable("GMAIL_APP_PASSWORD", "xxxx xxxx xxxx xxxx", "User")
```

Ręczna wysyłka samego pliku:

```powershell
python send_mail.py
```

---

## GitHub

Repozytorium: [github.com/Bigmax1993/scraping-sklepow](https://github.com/Bigmax1993/scraping-sklepow)

| Secret | Opis |
|--------|------|
| `GMAIL_USER` | Konto Gmail (nadawca) |
| `GMAIL_APP_PASSWORD` | Hasło do aplikacji Gmail |

Secrets są używane przez workflow **Run scraper** (uruchomienie ręczne: Actions → Run scraper → Run workflow). Pliki wynikowe trafiają też do artifactów GitHub Actions.

Lokalnie **nie commituj** haseł — trzymaj je w zmiennych Windows lub GitHub Secrets.

---

## Testy

```powershell
python -m pytest tests/ -v
```

**91 testów** w trzech warstwach:

```
tests/
├── unit/           # parsowanie, filtry, walidacja JSON, czyszczenie tekstu
├── integration/    # przepływ scrapera z mock HTTP, Excel E2E
├── regression/     # golden files (expected_regression.json)
└── fixtures/       # przykładowe HTML i JSON
```

Markery pytest: `unit`, `integration`, `regression`.

```powershell
python -m pytest tests/ -m unit -v
python -m pytest tests/unit/test_validation.py -v   # tylko walidacja
```

---

## Struktura projektu

```
Automatyczny scraping sklepow/
├── neueroeffnung_scraper.py      # główny skrypt
├── send_mail.py                  # wysyłka Excela przez Gmail
├── requirements.txt
├── pytest.ini
├── README.md                     # ta dokumentacja
├── docs/
│   └── ARCHITEKTURA.md           # szczegóły techniczne
├── tests/
├── neueroeffnung_wynik.xlsx      # wynik Excel (po uruchomieniu)
├── neueroeffnung_wynik.json      # wynik JSON (po uruchomieniu)
└── neueroeffnung_raport_brakow.json
```

---

## Rozwiązywanie problemów

| Problem | Rozwiązanie |
|---------|-------------|
| Pusty adres na stronie 2+ | Scraper szuka przez `/suche/` — sprawdź log |
| Pusta data zamknięcia | Normalne — pole opcjonalne |
| Status „Wymaga weryfikacji” | Zobacz arkusz **Raport braków** lub `neueroeffnung_raport_brakow.json` |
| Brak kolumny informacja | Usuń `neueroeffnung_detail_cache.json` i uruchom ponownie |
| Scraper trwa bardzo długo | Normalne przy pełnym skanie 4 kategorii × 30 stron + walidacja z ponowieniami |
| Zbyt długi czas / blokada IP | Zwiększ `REQUEST_DELAY_SEC` w skrypcie |

---

## Licencja i uwagi

Dane pochodzą z publicznego serwisu neueroeffnung.info. Używaj scrapera z poszanowaniem regulaminu serwisu i rozsądnym opóźnieniem między żądaniami.
