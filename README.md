# Germany temporarily closed markets scraper

Skrypt w Pythonie wykorzystujący Selenium do skrapowania Google Maps dla wybranych marek spożywczych w Niemczech.

Do CSV zapisywane są **wyłącznie sklepy oznaczone jako tymczasowo zamknięte**:

- polski: `tymczasowo zamknięte / tymczasowo zamkniete`
- niemiecki: `vorübergehend geschlossen / voruebergehend geschlossen`
- angielski: `temporarily closed`

## Uruchomienie

1. Zainstaluj zależności:

   ```bash
   pip install -r requirements.txt
   ```

2. Uruchom skrypt:

   ```bash
   python scraper.py
   ```

3. Wyniki:

- CSV: `C:\Users\kanbu\Documents\Budowy\germany_markets_selenium_closed_only.csv`
- cache JSON: `C:\Users\kanbu\Documents\Budowy\germany_markets_cache.json`
- logi: `C:\Users\kanbu\Documents\Budowy\germany_markets_scraper.log`

Skrypt można wznawiać – wykorzystuje istniejący CSV i cache JSON, dzięki czemu nie pobiera ponownie już znanych miejsc.
