# Scraping sklepow

Skrypt w Pythonie wykorzystujący Selenium do skrapowania Google Maps dla wybranych marek spozywczych w Niemczech.

Do CSV zapisywane sa **wylacznie sklepy oznaczone jako tymczasowo zamkniete**:

- polski: `tymczasowo zamkniete / tymczasowo zamknięte`
- niemiecki: `voruebergehend geschlossen / vorübergehend geschlossen`
- angielski: `temporarily closed`

## Najwazniejsze cechy

- Domyslnie dziala w tle (`headless`).
- Gdy pojawi sie CAPTCHA, skrypt przelacza sie na widoczna przegladarke do recznego potwierdzenia.
- Po rozwiazaniu CAPTCHA wraca do pracy w tle.
- Obsluguje wznowienie pracy na podstawie istniejacego CSV i cache JSON.
- Dziala z terminala oraz z Jupyter Lab.

## Instalacja

```bash
pip install -r requirements.txt
```

## Konfiguracja Gemini API

Skrypt odczytuje klucz z `GOOGLE_API_KEY`.

PowerShell:

```powershell
$env:GOOGLE_API_KEY="twoj_klucz_api"
```

CMD:

```cmd
set GOOGLE_API_KEY=twoj_klucz_api
```

## Uruchomienie reczne (lokalnie)

```bash
python scraper.py
```

## Uruchomienie z Jupyter Lab

```python
from scraper import run_scraper

run_scraper(headless_default=True, jupyter_mode=True)
```

W trybie Jupyter przy CAPTCHA:

1. Otwiera sie widoczna przegladarka.
2. Rozwiazujesz CAPTCHA recznie.
3. Potwierdzasz Enterem w notebooku.
4. Skrypt kontynuuje dzialanie.

## Wyniki

Pliki wynikowe zapisywane sa lokalnie w folderze `Wyniki/`:

- `Wyniki/germany_markets_selenium_closed_only.csv`
- `Wyniki/germany_markets_cache.json`
- `Wyniki/germany_markets_scraper.log`

Folder `Wyniki/` jest ignorowany przez Git i nie trafia do repozytorium.

## Testy

```bash
python -m pytest tests/test_status_parsing.py tests/test_captcha_and_driver.py -q
```

## GitHub Actions

Workflow znajduje sie w `.github/workflows/ci.yml` i oferuje:

- automatyczne uruchamianie testow na `push` i `pull_request`,
- reczne uruchamianie z poziomu zakladki **Actions** (`workflow_dispatch`),
- opcjonalne reczne odpalenie pelnego scrapera (`run_scraper=true`),
- automatyczny upload finalnego CSV do Google Drive.

Po dodaniu sekretu `GOOGLE_API_KEY` w ustawieniach repo mozna uruchomic scraper recznie z GitHub Actions.

## Upload CSV do Twojego Google Drive

Docelowy folder jest ustawiony na:
`1r9skaSfhfJ13xzkS8wIOkCKCRfY6VDd9`

Po zakonczeniu joba `run-scraper` plik
`Wyniki/germany_markets_selenium_closed_only.csv`
jest automatycznie wysylany do tego folderu.

### Co musisz ustawic raz

1. W Google Cloud utworz Service Account z dostepem do Google Drive API.
2. Wygeneruj klucz JSON.
3. Udostepnij folder Drive temu kontu serwisowemu (e-mail konta serwisowego, uprawnienie co najmniej Editor).
4. W repo GitHub dodaj secret:
   - `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON` -> wklej pelna zawartosc JSON klucza.

Skrypt uploadu to `upload_csv_to_drive.py`.
