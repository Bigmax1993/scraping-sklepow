# Google Drive — wynik Excel

## Docelowy folder

Po **Pipeline — Finalize** (oraz ręcznym **Build Excel → Google Drive**) plik `neueroeffnung_wynik.xlsx` trafia na:

[Neueroeffnung / scraping-sklepow](https://drive.google.com/drive/folders/1iwXppsgdZry3BHW46uSPKwOO3lqXBzOf)

| Plik na Drive | Zachowanie |
|---------------|------------|
| `neueroeffnung_wynik.xlsx` | Jeden plik — **nadpisywany** przy kolejnym udanym uploadzie (bez kopii z datą) |

Na Drive idzie **tylko Excel**. JSON, logi i staging zostają w artefaktach GitHub Actions.

---

## Secrets (GitHub)

| Secret | Opis |
|--------|------|
| `GDRIVE_FOLDER_ID` | ID folderu (`1iwXppsgdZry3BHW46uSPKwOO3lqXBzOf`) |
| `GDRIVE_OAUTH_CLIENT_ID` | OAuth Desktop client ID |
| `GDRIVE_OAUTH_CLIENT_SECRET` | OAuth Desktop client secret |
| `GDRIVE_OAUTH_REFRESH_TOKEN` | Refresh token (upload w imieniu użytkownika) |

Te same sekrety OAuth co w **Kampania-Chiny** (wspólne konto Google). Folder ID jest osobny dla tego projektu.

### Jednorazowa konfiguracja OAuth (gdy trzeba odnowić token)

```powershell
pip install -r requirements-drive.txt
# Ustaw GDRIVE_OAUTH_CLIENT_ID i GDRIVE_OAUTH_CLIENT_SECRET w env
python scripts/gdrive_oauth_setup.py
```

Skrypt otworzy przeglądarkę i ustawi `GDRIVE_OAUTH_*` w secrets repo `Bigmax1993/scraping-sklepow`.

---

## Kiedy upload się dzieje

| Workflow | Kiedy | Źródło Excela |
|----------|-------|---------------|
| **Pipeline — Finalize** | Nd 12:00 PL (cron) lub ręcznie | Claude + rekordy `po_scrape_kontakt` → `neueroeffnung_wynik.xlsx` → Drive |
| **Build Excel → Google Drive** | Tylko ręcznie | Artefakt `pipeline-staging-discovery` → Excel → Drive (bez Claude/mail) |

Finalize wymaga rekordów na etapie `po_scrape_kontakt` w cache/staging. Jeśli staging jest pusty, Excel nie powstanie i krok Drive się nie wykona poprawnie — wtedy użyj **Build Excel → Google Drive** z ID runu Discovery.

---

## Skrypty

| Plik | Rola |
|------|------|
| `scripts/gdrive_upload.py` | Upload/overwrite `neueroeffnung_wynik.xlsx` do `GDRIVE_FOLDER_ID` |
| `scripts/gdrive_oauth_setup.py` | Jednorazowy OAuth → GitHub Secrets |
| `scripts/build_excel_from_staging.py` | Excel ze `neueroeffnung_staging.json` (bez Claude) |
| `requirements-drive.txt` | `google-api-python-client`, `google-auth`, `google-auth-oauthlib` |

Lokalnie (po zbudowaniu xlsx i ustawieniu sekretów w env):

```powershell
python scripts/gdrive_upload.py
```

---

## Ręczny deploy z artefaktu Discovery

1. Actions → **Build Excel → Google Drive** → **Run workflow**
2. Podaj `discovery_run_id` (np. z URL runu Discovery: `.../actions/runs/34090527280`)
3. Po sukcesie sprawdź folder Drive i artefakt `neueroeffnung-wynik-from-discovery`

```powershell
gh workflow run "Build Excel → Google Drive" -R Bigmax1993/scraping-sklepow -f discovery_run_id=34090527280
```
