import csv
import re
import time
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote_plus, urljoin
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# =========================
# KONFIG
# =========================
try:
    PROJECT_ROOT = Path(__file__).resolve().parent
except NameError:
    # Jupyter/interactive mode: __file__ is not available.
    PROJECT_ROOT = Path.cwd()
OUTPUT_DIR = PROJECT_ROOT / "Wyniki"
OUTPUT_FILE = OUTPUT_DIR / "germany_markets_selenium_closed_only.csv"
CACHE_FILE = OUTPUT_DIR / "germany_markets_cache.json"
LOG_FILE = OUTPUT_DIR / "germany_markets_scraper.log"

BRANDS = ["rewe", "edeka", "penny", "netto", "aldi"]

# Niemcy bbox
LAT_MIN, LAT_MAX = 47.3, 54.9
LON_MIN, LON_MAX = 6.0, 14.9
LAT_STEP = 0.9
LON_STEP = 1.1

MAX_SCROLL_ROUNDS = 25
SCROLL_PAUSE = 1.0
HEADLESS_DEFAULT = True
CAPTCHA_CHECK_TIMEOUT = 600  # sekundy
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_TIMEOUT = 25
GEMINI_CALLS_PER_RECORD = 3
_GEMINI_MISSING_KEY_WARNED = False


class CaptchaRequired(Exception):
    pass


def is_running_in_jupyter():
    try:
        from IPython import get_ipython

        shell = get_ipython()
        if shell is None:
            return False
        return shell.__class__.__name__ == "ZMQInteractiveShell"
    except Exception:
        return False


def wait_for_user_confirmation(message, jupyter_mode=False):
    if jupyter_mode:
        print(message)
        print("W Jupyter wpisz cokolwiek i naciśnij Enter, aby kontynuować.")
    else:
        print(message)
    input("> ")


def setup_logging():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("germany_scraper")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


def frange(start, stop, step):
    v = start
    while v <= stop:
        yield round(v, 4)
        v += step


def save_csv(rows, path):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "marka",
        "nazwa",
        "ocena",
        "liczba_opinii",
        "kategoria",
        "adres",
        "full_address",
        "status",
        "telefon",
        "www",
        "url",
        # UWAGA: sklepy należy grupować po kolumnie "grupa_generalnego_wykonawcy".
        "generalny_wykonawca",
        "grupa_generalnego_wykonawcy",
        # Dodatkowe grupowanie: wykonawca w podziale na niemieckie landy.
        "land_niemiecki",
        "grupa_wykonawcy_w_landzie",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def load_existing_csv(path, logger):
    rows = []
    seen_urls = set()
    if not path.exists():
        return rows, seen_urls
    logger.info(f"Ładowanie istniejącego CSV: {path}")
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for r in reader:
            rows.append(r)
            if "url" in r and r["url"]:
                seen_urls.add(r["url"])
    logger.info(f"Wczytano {len(rows)} rekordów z CSV (seen_global={len(seen_urls)})")
    return rows, seen_urls


def load_cache(logger):
    if not CACHE_FILE.exists():
        logger.info("Brak istniejącego cache JSON – zaczynam od zera.")
        return {"places": {}}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        if "places" not in cache:
            cache["places"] = {}
        logger.info(f"Wczytano cache JSON: {len(cache['places'])} miejsc.")
        return cache
    except Exception as e:
        logger.warning(f"Nie udało się wczytać cache JSON ({e}) – tworzę nowy.")
        return {"places": {}}


def save_cache(cache, logger):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        logger.info(f"Zapisano cache JSON: {len(cache.get('places', {}))} miejsc.")
    except Exception as e:
        logger.error(f"Błąd zapisu cache JSON: {e}")


def parse_card_text(raw):
    parts = [p.strip() for p in raw.split("·") if p.strip()]
    kategoria = parts[0] if len(parts) > 0 else ""
    adres = parts[1] if len(parts) > 1 else ""
    status = parts[2] if len(parts) > 2 else ""
    return kategoria, adres, status


def click_if_exists(driver, by, value):
    try:
        el = driver.find_element(by, value)
        el.click()
        return True
    except Exception:
        return False


def dismiss_consent(driver):
    candidates = [
        (By.XPATH, "//button[contains(., 'Accept all')]"),
        (By.XPATH, "//button[contains(., 'Zaakceptuj wszystko')]"),
        (By.XPATH, "//button[contains(., 'Alle akzeptieren')]"),
        (By.XPATH, "//button[contains(., 'I agree')]"),
    ]
    for by, val in candidates:
        if click_if_exists(driver, by, val):
            time.sleep(1)
            break


def search_url(brand, lat, lon, zoom=10.5):
    return f"https://www.google.com/maps/search/{quote_plus(brand + ' deutschland')}/@{lat},{lon},{zoom}z"


def build_driver(headless=True):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    else:
        options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)


def is_captcha_page(driver):
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        url = ""

    try:
        title = (driver.title or "").lower()
    except Exception:
        title = ""

    if any(x in url for x in ["/sorry/", "sorry/index", "recaptcha"]):
        return True
    if any(x in title for x in ["unusual traffic", "recaptcha", "robot check"]):
        return True

    captcha_xpaths = [
        "//iframe[contains(@src, 'recaptcha')]",
        "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'i am not a robot')]",
        "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'unusual traffic')]",
        "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'reCAPTCHA')]",
    ]
    for xp in captcha_xpaths:
        try:
            if driver.find_elements(By.XPATH, xp):
                return True
        except Exception:
            continue
    return False


def transfer_cookies(source_driver, target_driver):
    try:
        cookies = source_driver.get_cookies()
    except Exception:
        return
    for cookie in cookies:
        try:
            target_driver.add_cookie(cookie)
        except Exception:
            continue


def handle_captcha(driver, logger, jupyter_mode=False):
    logger.warning("Wykryto CAPTCHA. Przełączam na widoczną przeglądarkę do ręcznego potwierdzenia.")
    print("\n[CAPTCHA] Wykryto CAPTCHA - otwieram przeglądarkę do ręcznego potwierdzenia.")
    current_url = ""
    try:
        current_url = driver.current_url
    except Exception:
        pass

    visible_driver = None
    try:
        visible_driver = build_driver(headless=False)
        visible_driver.get("https://www.google.com")
        transfer_cookies(driver, visible_driver)
        visible_driver.get(current_url or "https://www.google.com/maps")

        wait_for_user_confirmation(
            "[CAPTCHA] Rozwiąż CAPTCHA w otwartym oknie. Po zakończeniu potwierdź tutaj.",
            jupyter_mode=jupyter_mode,
        )

        wait_start = time.time()
        while is_captcha_page(visible_driver):
            if (time.time() - wait_start) > CAPTCHA_CHECK_TIMEOUT:
                raise TimeoutException("Przekroczono czas oczekiwania na rozwiązanie CAPTCHA.")
            wait_for_user_confirmation(
                "[CAPTCHA] Nadal wykrywam CAPTCHA. Dokończ w przeglądarce i potwierdź ponownie.",
                jupyter_mode=jupyter_mode,
            )

        headless_driver = build_driver(headless=True)
        headless_driver.get("https://www.google.com")
        transfer_cookies(visible_driver, headless_driver)
        if current_url:
            headless_driver.get(current_url)
        logger.info("CAPTCHA rozwiązana. Powrót do pracy w tle.")
        print("[CAPTCHA] CAPTCHA rozwiązana. Wracam do trybu tła.\n")
        return headless_driver
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        if visible_driver is not None:
            try:
                visible_driver.quit()
            except Exception:
                pass


def scroll_results_panel(driver):
    panel = None
    try:
        panel = driver.find_element(By.XPATH, "//div[@role='feed']")
    except NoSuchElementException:
        pass

    prev_count = 0
    stable = 0

    for _ in range(MAX_SCROLL_ROUNDS):
        cards = driver.find_elements(By.XPATH, "//a[contains(@href, '/maps/place/')]")
        count_now = len(cards)

        if count_now <= prev_count:
            stable += 1
        else:
            stable = 0
        prev_count = count_now

        if stable >= 4:
            break

        if panel is not None:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", panel)
        else:
            driver.execute_script("window.scrollBy(0, 3000);")

        time.sleep(SCROLL_PAUSE)


def extract_open_status(text: str) -> str:
    if not text:
        return ""

    t = " ".join(text.split()).strip()
    tl = t.lower()

    if "otwarte" in tl:
        return "Otwarte"
    if "tymczasowo zamknięte" in tl or "tymczasowo zamkniete" in tl:
        return "Tymczasowo zamknięte"
    if "zamknięte" in tl or "zamkniete" in tl:
        return "Zamknięte"

    if "geöffnet" in tl or "geoeffnet" in tl:
        return "Geöffnet"
    if "vorübergehend geschlossen" in tl or "voruebergehend geschlossen" in tl:
        return "Vorübergehend geschlossen"
    if "geschlossen" in tl:
        return "Geschlossen"

    if "temporarily closed" in tl:
        return "Temporarily closed"
    if "open" in tl:
        return "Open"
    if "closed" in tl:
        return "Closed"

    return ""


def is_closed_status(status: str) -> bool:
    """
    Zwraca True TYLKO dla statusów typu 'tymczasowo zamknięte' (PL/DE/EN),
    ignoruje zwykłe 'zamknięte/closed/geschlossen'.
    """
    s = (status or "").strip().lower()
    return any(
        x in s
        for x in [
            "tymczasowo zamknięte",
            "tymczasowo zamkniete",
            "vorübergehend geschlossen",
            "voruebergehend geschlossen",
            "temporarily closed",
        ]
    )


def get_general_contractor_with_gemini(brand, name, full_address, address, logger):
    global _GEMINI_MISSING_KEY_WARNED

    if not GEMINI_API_KEY:
        if not _GEMINI_MISSING_KEY_WARNED:
            logger.warning(
                "Brak GEMINI_API_KEY. Pole 'generalny_wykonawca' pozostanie puste."
            )
            _GEMINI_MISSING_KEY_WARNED = True
        return ""

    location = full_address or address or ""
    prompt = (
        "Zwróć wyłącznie nazwę Generalnego Wykonawcy (firma budowlana), "
        "jeśli da się ją wiarygodnie ustalić dla poniższego sklepu. "
        "Jeśli brak wiarygodnych danych, zwróć dokładnie: BRAK_DANYCH.\n\n"
        f"Marka sklepu: {brand}\n"
        f"Nazwa sklepu: {name}\n"
        f"Adres: {location}\n"
    )

    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        f"?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 120},
    }

    responses = []
    for _ in range(GEMINI_CALLS_PER_RECORD):
        try:
            req = Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=GEMINI_TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
            logger.warning(f"Gemini API - błąd dla sklepu '{name}': {e}")
            continue
        except Exception as e:
            logger.warning(f"Gemini API - nieoczekiwany błąd dla sklepu '{name}': {e}")
            continue

        text = ""
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                text = (parts[0].get("text") or "").strip()

        if text and text.upper() != "BRAK_DANYCH":
            cleaned = text.replace("\n", " ").strip().strip('"')
            if cleaned:
                responses.append(cleaned)

    if not responses:
        return ""

    # Zwróć najczęściej powtarzającą się odpowiedź z 3 wywołań.
    return max(set(responses), key=responses.count)


def get_german_state_with_gemini(brand, name, full_address, address, logger):
    global _GEMINI_MISSING_KEY_WARNED

    if not GEMINI_API_KEY:
        if not _GEMINI_MISSING_KEY_WARNED:
            logger.warning(
                "Brak GEMINI_API_KEY. Pole 'land_niemiecki' pozostanie puste."
            )
            _GEMINI_MISSING_KEY_WARNED = True
        return ""

    location = full_address or address or ""
    prompt = (
        "Zwróć wyłącznie nazwę niemieckiego landu (Bundesland) dla poniższego sklepu. "
        "Użyj standardowej nazwy landu po niemiecku, np. Bayern, Nordrhein-Westfalen, Sachsen. "
        "Jeśli brak wiarygodnych danych, zwróć dokładnie: BRAK_DANYCH.\n\n"
        f"Marka sklepu: {brand}\n"
        f"Nazwa sklepu: {name}\n"
        f"Adres: {location}\n"
    )

    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        f"?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 80},
    }

    responses = []
    for _ in range(GEMINI_CALLS_PER_RECORD):
        try:
            req = Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=GEMINI_TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
            logger.warning(f"Gemini API - błąd landu dla sklepu '{name}': {e}")
            continue
        except Exception as e:
            logger.warning(f"Gemini API - nieoczekiwany błąd landu dla sklepu '{name}': {e}")
            continue

        text = ""
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                text = (parts[0].get("text") or "").strip()

        if text and text.upper() != "BRAK_DANYCH":
            cleaned = text.replace("\n", " ").strip().strip('"')
            if cleaned:
                responses.append(cleaned)

    if not responses:
        return ""

    return max(set(responses), key=responses.count)


def extract_details_in_new_tab(driver, url):
    phone = ""
    website = ""
    status = ""
    full_address = ""

    base_handle = driver.current_window_handle
    driver.execute_script("window.open(arguments[0], '_blank');", url)
    driver.switch_to.window(driver.window_handles[-1])

    try:
        time.sleep(1.5)
        if is_captcha_page(driver):
            raise CaptchaRequired("CAPTCHA w widoku szczegółów miejsca.")

        tel_links = driver.find_elements(By.XPATH, "//a[starts-with(@href,'tel:')]")
        if tel_links:
            href = tel_links[0].get_attribute("href") or ""
            phone = href.replace("tel:", "").strip()

        for xp in [
            "//a[contains(., 'Website')]",
            "//a[contains(., 'Witryna')]",
            "//a[contains(., 'Webseite')]",
        ]:
            els = driver.find_elements(By.XPATH, xp)
            if els:
                h = els[0].get_attribute("href")
                if h:
                    website = h
                    break

        addr_candidates = driver.find_elements(By.XPATH, "//*[@data-item-id='address']")
        for el in addr_candidates:
            txt = (el.text or "").strip()
            if txt and len(txt) > 6:
                full_address = " ".join(txt.split())
                break

        if not full_address:
            fallback_xpaths = [
                "//*[@aria-label[contains(., 'Address')]]",
                "//*[@aria-label[contains(., 'Adres')]]",
                "//*[@aria-label[contains(., 'Adresse')]]",
            ]
            for xp in fallback_xpaths:
                els = driver.find_elements(By.XPATH, xp)
                for el in els:
                    txt = (el.text or "").strip()
                    if txt and len(txt) > 6:
                        full_address = " ".join(txt.split())
                        break
                if full_address:
                    break

        candidate_texts = []
        status_xpaths = [
            "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZĄĆĘŁŃÓŚŹŻ','abcdefghijklmnopqrstuvwxyząćęłńóśźż'),'otwarte') or contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZĄĆĘŁŃÓŚŹŻ','abcdefghijklmnopqrstuvwxyząćęłńóśźż'),'zamknięte') or contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZĄĆĘŁŃÓŚŹŻ','abcdefghijklmnopqrstuvwxyząćęłńóśźż'),'zamkniete')]",
            "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜẞ','abcdefghijklmnopqrstuvwxyzäöüß'),'geöffnet') or contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜẞ','abcdefghijklmnopqrstuvwxyzäöüß'),'geoeffnet') or contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜẞ','abcdefghijklmnopqrstuvwxyzäöüß'),'geschlossen')]",
            "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'open') or contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'closed')]",
        ]

        for xp in status_xpaths:
            els = driver.find_elements(By.XPATH, xp)
            for el in els[:12]:
                txt = (el.text or "").strip()
                if txt:
                    candidate_texts.append(txt)

        if not candidate_texts:
            panel_candidates = driver.find_elements(By.XPATH, "//div[@role='main'] | //body")
            for el in panel_candidates[:2]:
                txt = (el.text or "").strip()
                if txt:
                    candidate_texts.append(txt)

        for txt in candidate_texts:
            s = extract_open_status(txt)
            if s:
                status = s
                break

    except Exception:
        pass
    finally:
        driver.close()
        driver.switch_to.window(base_handle)

    return phone, website, status, full_address


def get_place_details_with_cache(driver, url, cache, logger):
    places = cache.setdefault("places", {})
    if url in places:
        data = places[url]
        return (
            data.get("phone", ""),
            data.get("website", ""),
            data.get("status", ""),
            data.get("full_address", ""),
        )

    phone, website, status, full_address = extract_details_in_new_tab(driver, url)
    places[url] = {
        "phone": phone,
        "website": website,
        "status": status,
        "full_address": full_address,
    }
    logger.info(f"Dodano do cache: {url}")
    return phone, website, status, full_address


def scrape_brand_cell(driver, brand, lat, lon, cache, logger):
    logger.info(f"Start komórki: brand={brand}, lat={lat}, lon={lon}")
    driver.get(search_url(brand, lat, lon))
    time.sleep(3)
    if is_captcha_page(driver):
        raise CaptchaRequired("CAPTCHA po wejściu na stronę wyszukiwania.")

    dismiss_consent(driver)

    try:
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.XPATH, "//a[contains(@href, '/maps/place/')]"))
        )
    except TimeoutException:
        if is_captcha_page(driver):
            raise CaptchaRequired("CAPTCHA zamiast listy wyników.")
        logger.warning(f"Timeout – brak wyników dla brand={brand}, lat={lat}, lon={lon}")
        return []

    for xp in [
        "//button[contains(., 'Szukaj w tym obszarze')]",
        "//button[contains(., 'Search this area')]",
        "//button[contains(., 'In diesem Bereich suchen')]",
    ]:
        if click_if_exists(driver, By.XPATH, xp):
            logger.info("Kliknięto 'Szukaj w tym obszarze'")
            time.sleep(2)
            break

    scroll_results_panel(driver)

    cards = driver.find_elements(By.XPATH, "//a[contains(@href, '/maps/place/')]")
    rows = []
    seen_local = set()

    logger.info(f"Znaleziono {len(cards)} kart dla brand={brand}, lat={lat}, lon={lon}")

    for card in cards:
        href = card.get_attribute("href") or ""
        if not href:
            continue

        place_url = urljoin("https://www.google.com", href)
        if place_url in seen_local:
            continue
        seen_local.add(place_url)

        try:
            raw = card.text.strip()
        except Exception:
            raw = ""

        try:
            h3 = card.find_element(By.XPATH, ".//h3")
            name = h3.text.strip()
        except Exception:
            name = ""

        rating = ""
        reviews = ""
        m_rating = re.search(r"(\d[.,]\d)", raw)
        if m_rating:
            rating = m_rating.group(1).replace(",", ".")

        m_reviews = re.search(r"\(([\d\s.,]+)\)", raw)
        if m_reviews:
            reviews = m_reviews.group(1).replace(" ", "")

        category, address, status_from_list = parse_card_text(raw)
        phone, website, status_from_detail, full_address = get_place_details_with_cache(
            driver, place_url, cache, logger
        )

        status = status_from_detail if status_from_detail else status_from_list
        general_contractor = get_general_contractor_with_gemini(
            brand=brand.upper(),
            name=name,
            full_address=full_address,
            address=address,
            logger=logger,
        )
        german_state = get_german_state_with_gemini(
            brand=brand.upper(),
            name=name,
            full_address=full_address,
            address=address,
            logger=logger,
        )
        contractor_group_by_state = (
            f"{german_state} | {general_contractor}"
            if german_state and general_contractor
            else ""
        )

        rows.append(
            {
                "marka": brand.upper(),
                "nazwa": name,
                "ocena": rating,
                "liczba_opinii": reviews,
                "kategoria": category,
                "adres": address,
                "full_address": full_address,
                "status": status,
                "telefon": phone,
                "www": website,
                "url": place_url,
                "lat_center": lat,
                "lon_center": lon,
                "generalny_wykonawca": general_contractor,
                "grupa_generalnego_wykonawcy": general_contractor,
                "land_niemiecki": german_state,
                "grupa_wykonawcy_w_landzie": contractor_group_by_state,
            }
        )

    return rows


def run_scraper(headless_default=HEADLESS_DEFAULT, jupyter_mode=None):
    if jupyter_mode is None:
        jupyter_mode = is_running_in_jupyter()

    logger = setup_logging()
    logger.info("=== START skryptu Google Maps Niemcy (zamknięte) ===")
    logger.info(f"Tryb Jupyter: {'TAK' if jupyter_mode else 'NIE'}")

    driver = build_driver(headless=headless_default)

    all_rows, seen_global = load_existing_csv(OUTPUT_FILE, logger)
    cache = load_cache(logger)

    try:
        grid_points = [
            (lat, lon)
            for lat in frange(LAT_MIN, LAT_MAX, LAT_STEP)
            for lon in frange(LON_MIN, LON_MAX, LON_STEP)
        ]
        logger.info(f"Punktów siatki: {len(grid_points)}")
        print(f"Punktów siatki: {len(grid_points)}")

        for idx, (lat, lon) in enumerate(grid_points, start=1):
            logger.info(f"=== Komórka {idx}/{len(grid_points)} | lat={lat}, lon={lon} ===")
            print(f"\n=== Komórka {idx}/{len(grid_points)} | lat={lat}, lon={lon} ===")

            for brand in BRANDS:
                captcha_retries = 0
                while True:
                    try:
                        rows = scrape_brand_cell(driver, brand, lat, lon, cache, logger)
                        added = 0

                        for r in rows:
                            if not is_closed_status(r.get("status", "")):
                                continue
                            if r["url"] in seen_global:
                                continue
                            seen_global.add(r["url"])
                            all_rows.append(r)
                            added += 1

                        logger.info(f"{brand.upper()}: +{added} (zamknięte) w tej komórce")
                        print(f"{brand.upper()}: +{added} (zamknięte)")
                        save_csv(all_rows, OUTPUT_FILE)
                        save_cache(cache, logger)
                        break

                    except CaptchaRequired as e:
                        captcha_retries += 1
                        logger.warning(f"{brand.upper()}: {e} (próba {captcha_retries})")
                        if captcha_retries > 3:
                            logger.error(f"{brand.upper()}: zbyt wiele CAPTCHA, pomijam brand.")
                            print(f"{brand.upper()}: zbyt wiele CAPTCHA, pomijam.")
                            break
                        driver = handle_captcha(driver, logger, jupyter_mode=jupyter_mode)
                        time.sleep(2)
                        continue
                    except Exception as e:
                        logger.exception(f"{brand.upper()}: błąd")
                        print(f"{brand.upper()}: błąd ({e})")
                        break

    finally:
        driver.quit()
        logger.info("Zamknięto przeglądarkę.")

    logger.info(f"Gotowe. Zapisano {len(all_rows)} rekordów (zamknięte) do: {OUTPUT_FILE}")
    logger.info('UWAGA: sklepy należy pogrupować po kolumnie "grupa_generalnego_wykonawcy".')
    print(f"\nGotowe. Zapisano {len(all_rows)} rekordów (zamknięte) do: {OUTPUT_FILE}")


def main():
    run_scraper(headless_default=HEADLESS_DEFAULT, jupyter_mode=False)


if __name__ == "__main__":
    main()

