# Google Drive — wynik Excel (tylko Finalize)

## Zasada

Na Google Drive trafia **wyłącznie** pełny wynik z **Pipeline — Finalize**:

1. rekordy po całym łańcuchu `discovery → validated → po_maps → po_scrape_kontakt`
2. filtr Claude + walidacja + Excel 8 arkuszy
3. e-mail (opcjonalnie) **oraz** upload `neueroeffnung_wynik.xlsx`

**Nie** uploadujemy Excela z Discovery / Validate / Maps / Contact ani z ręcznego „build from staging”.

## Docelowy folder

[Neueroeffnung / scraping-sklepow](https://drive.google.com/drive/folders/1iwXppsgdZry3BHW46uSPKwOO3lqXBzOf)

| Plik na Drive | Zachowanie |
|---------------|------------|
| `neueroeffnung_wynik.xlsx` | Jeden plik — **nadpisywany** tylko po udanym Finalize |

JSON, logi i staging zostają w artefaktach GitHub Actions (`neueroeffnung-wynik`, `pipeline-state`).

---

## Secrets (GitHub)

| Secret | Opis |
|--------|------|
| `GDRIVE_FOLDER_ID` | ID folderu (`1iwXppsgdZry3BHW46uSPKwOO3lqXBzOf`) |
| `GDRIVE_OAUTH_CLIENT_ID` | OAuth Desktop client ID |
| `GDRIVE_OAUTH_CLIENT_SECRET` | OAuth Desktop client secret |
| `GDRIVE_OAUTH_REFRESH_TOKEN` | Refresh token |

OAuth jak w **Kampania-Chiny**; folder ID jest osobny dla tego projektu.

### Odnowienie tokena

```powershell
pip install -r requirements-drive.txt
# Ustaw GDRIVE_OAUTH_CLIENT_ID i GDRIVE_OAUTH_CLIENT_SECRET w env
python scripts/gdrive_oauth_setup.py
```

---

## Kiedy upload się dzieje

| Workflow | Upload Drive? |
|----------|---------------|
| Pipeline — Finalize | **Tak** — tylko gdy powstał `neueroeffnung_wynik.xlsx` |
| Discovery / Validate / Maps / Contact | Nie |
| Run scraper (full) | Nie (dev); produkcja = segment Finalize |

Finalize (Nd **12:00** PL w tygodniu cyklu 28-dniowego, lub ręcznie): Claude → Excel → mail → **Drive**.

Jeśli brak rekordów `po_scrape_kontakt`, Excel nie powstaje i krok Drive jest pomijany (`hashFiles`).

Pierwszy automatyczny Finalize cyklu: **2026-10-11**.

---

## Skrypty

| Plik | Rola |
|------|------|
| `scripts/gdrive_upload.py` | Upload/overwrite Excel do `GDRIVE_FOLDER_ID` |
| `scripts/gdrive_oauth_setup.py` | Jednorazowy OAuth → GitHub Secrets |
| `requirements-drive.txt` | Zależności Google API |

`scripts/build_excel_from_staging.py` buduje Excel ze staging **lokalnie / diagnostycznie** — **nie** jest podpięty do Drive w CI.
