"""
Automatyczne wystawianie ogłoszeń na Autoplac.pl przy użyciu Playwright.

Playwright steruje prawdziwą przeglądarką (Chromium), dzięki czemu
omija podstawową ochronę anty-botową i obsługuje formularze z JS.

UWAGA: Autoplac.pl może zmieniać selektory pól formularza.
Jeśli coś przestanie działać – zaktualizuj słownik SELECTORS.
Wskazówka: DevTools (F12 → Inspector) → kliknij pole → skopiuj selektor.
"""

import logging
import time
from pathlib import Path
from typing import Callable, Optional

from otomoto_scraper import CarListing

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapowania: wartości z OtoMoto → etykiety opcji na Autoplac.pl
# ---------------------------------------------------------------------------

FUEL_TYPE_MAP = {
    "benzyna":      "Benzyna",
    "petrol":       "Benzyna",
    "gasoline":     "Benzyna",
    "diesel":       "Diesel",
    "electric":     "Elektryczny",
    "elektryczny":  "Elektryczny",
    "hybrid":       "Hybryda",
    "hybryda":      "Hybryda",
    "benzyna+lpg":  "LPG",
    "lpg":          "LPG",
    "cng":          "CNG",
    "benzyna+cng":  "CNG",
    "hydrogen":     "Wodór",
}

GEARBOX_MAP = {
    "manualna":     "Manualna",
    "manual":       "Manualna",
    "automatyczna": "Automatyczna",
    "automatic":    "Automatyczna",
}

BODY_TYPE_MAP = {
    "sedan":      "Sedan",
    "kombi":      "Kombi",
    "hatchback":  "Hatchback",
    "suv":        "SUV",
    "coupe":      "Coupe",
    "cabrio":     "Kabriolet",
    "kabriolet":  "Kabriolet",
    "van":        "Van",
    "minivan":    "Minivan",
    "pickup":     "Pick-up",
    "kompakt":    "Hatchback",
}

CONDITION_MAP = {
    "używany":    "Używany",
    "used":       "Używany",
    "nowy":       "Nowy",
    "new":        "Nowy",
    "damaged":    "Uszkodzony",
    "uszkodzony": "Uszkodzony",
}

# ---------------------------------------------------------------------------
# Selektory formularza Autoplac.pl  ← ZAKTUALIZUJ JEŚLI COŚ SIĘ PSUJE
# ---------------------------------------------------------------------------

SELECTORS = {
    # Logowanie
    "login_email":    "input[name='email'], input[type='email']",
    "login_password": "input[name='password'], input[type='password']",
    "login_submit":   "button[type='submit'], input[type='submit']",

    # Pola tekstowe ogłoszenia
    "field_title":    "input[name='title'], #title, input[placeholder*='tytuł' i]",
    "field_price":    "input[name='price'], #price, input[placeholder*='cena' i]",
    "field_year":     "input[name='year'], input[name='rok'], #year",
    "field_mileage":  "input[name='mileage'], input[name='przebieg'], #mileage",
    "field_engine":   "input[name='engine_capacity'], input[name='pojemnosc'], #engine_capacity",
    "field_power":    "input[name='engine_power'], input[name='moc'], #engine_power",
    "field_desc":     "textarea[name='description'], textarea[name='opis'], #description",

    # Listy rozwijane
    "select_make":      "select[name='make'], select[name='marka'], #make",
    "select_model":     "select[name='model'], #model",
    "select_fuel":      "select[name='fuel_type'], select[name='paliwo'], #fuel_type",
    "select_gearbox":   "select[name='gearbox'], select[name='skrzynia'], #gearbox",
    "select_body_type": "select[name='body_type'], select[name='nadwozie'], #body_type",
    "select_condition": "select[name='state'], select[name='stan'], #condition",
    "select_color":     "select[name='color'], select[name='kolor'], #color",

    # Upload zdjęć i przycisk wysyłki
    "photo_input": "input[type='file']",
    "submit":      "button[type='submit']:text('Dodaj ogłoszenie'), button[type='submit']:text('Zapisz'), button[type='submit']",
}

AUTOPLAC_BASE_URL = "https://www.autoplac.pl"
ADD_LISTING_PATHS = [
    "/dodaj-ogloszenie/",
    "/konto/dodaj/",
    "/ogloszenia/dodaj/",
    "/panel/dodaj-ogloszenie/",
    "/dodaj/",
]

# Czas (ms) oczekiwania na elementy formularza
TIMEOUT_MS = 15_000


def _log(msg: str, callback: Optional[Callable] = None) -> None:
    logger.info(msg)
    if callback:
        callback(msg)


def _map(value: str, mapping: dict) -> str:
    return mapping.get(value.strip().lower(), value.strip())


def _fill(page, selector: str, value: str, label: str, cb=None) -> bool:
    try:
        el = page.query_selector(selector)
        if not el:
            return False
        el.triple_click()
        el.fill(value)
        _log(f"  ✓ {label}: {value}", cb)
        return True
    except Exception as exc:
        logger.debug("Błąd fill '%s': %s", label, exc)
        return False


def _select(page, selector: str, value: str, label: str, cb=None) -> bool:
    if not value:
        return False
    try:
        el = page.query_selector(selector)
        if not el:
            return False
        # Próbuj po label, potem po value
        try:
            page.select_option(selector, label=value)
        except Exception:
            page.select_option(selector, value=value)
        _log(f"  ✓ {label}: {value}", cb)
        return True
    except Exception as exc:
        logger.debug("Błąd select '%s' = '%s': %s", label, value, exc)
        return False


def post_to_autoplac(
    listing: CarListing,
    photo_paths: list[Path],
    email: str,
    password: str,
    headless: bool = True,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Loguje się do Autoplac.pl i wystawia ogłoszenie.

    Args:
        listing:           Dane ogłoszenia (wypełnione przez scraper lub edytowane przez użytkownika).
        photo_paths:       Ścieżki do pobranych zdjęć.
        email:             Login do Autoplac.pl.
        password:          Hasło do Autoplac.pl.
        headless:          True = przeglądarka w tle; False = widoczna (do debugowania).
        progress_callback: Funkcja(str) wywoływana z komunikatami postępu.

    Returns:
        {"success": bool, "message": str, "url": str|None}
    """
    cb = progress_callback

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return {
            "success": False,
            "message": (
                "Playwright nie jest zainstalowany.\n"
                "Uruchom: pip install playwright && playwright install chromium"
            ),
            "url": None,
        }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pl-PL",
            viewport={"width": 1280, "height": 900},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = ctx.new_page()

        # ── 1. Logowanie ──────────────────────────────────────────────────
        _log("Otwieram Autoplac.pl...", cb)
        page.goto(AUTOPLAC_BASE_URL, wait_until="domcontentloaded", timeout=60_000)
        time.sleep(1)

        # Przejdź do strony logowania
        if not any(k in page.url for k in ["login", "logowanie"]):
            for href in ["/logowanie/", "/login/", "/konto/logowanie/"]:
                link = page.query_selector(f"a[href*='{href.strip('/')}']")
                if link:
                    link.click()
                    page.wait_for_load_state("domcontentloaded")
                    break
            else:
                page.goto(f"{AUTOPLAC_BASE_URL}/logowanie/", wait_until="domcontentloaded")

        time.sleep(0.8)
        _log("Loguję się...", cb)
        _fill(page, SELECTORS["login_email"], email, "e-mail")
        _fill(page, SELECTORS["login_password"], password, "hasło")

        btn = page.query_selector(SELECTORS["login_submit"])
        if btn:
            btn.click()
        else:
            page.keyboard.press("Enter")

        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeout:
            pass
        time.sleep(1.5)

        if any(k in page.url for k in ["login", "logowanie"]):
            browser.close()
            return {
                "success": False,
                "message": "Logowanie nie powiodło się. Sprawdź e-mail i hasło do Autoplac.pl.",
                "url": None,
            }
        _log("Zalogowano pomyślnie.", cb)

        # ── 2. Formularz dodawania ogłoszenia ────────────────────────────
        _log("Szukam formularza...", cb)
        form_found = False
        for path in ADD_LISTING_PATHS:
            url = AUTOPLAC_BASE_URL + path
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            time.sleep(0.8)
            if (page.query_selector(SELECTORS["field_title"])
                    or page.query_selector(SELECTORS["field_price"])):
                form_found = True
                _log(f"Formularz znaleziony: {url}", cb)
                break

        if not form_found:
            browser.close()
            return {
                "success": False,
                "message": (
                    "Nie znaleziono formularza dodawania ogłoszenia.\n"
                    "Zaktualizuj ADD_LISTING_PATHS w autoplac_poster.py."
                ),
                "url": None,
            }

        # ── 3. Wypełnianie danych ─────────────────────────────────────────
        _log("Wypełniam dane ogłoszenia...", cb)

        _fill(page, SELECTORS["field_title"], listing.title, "tytuł", cb)

        if listing.make and _select(page, SELECTORS["select_make"], listing.make, "marka", cb):
            time.sleep(1)  # modele ładowane dynamicznie
        if listing.model:
            _select(page, SELECTORS["select_model"], listing.model, "model", cb)

        _fill(page, SELECTORS["field_year"], listing.year, "rok", cb)

        mileage = listing.mileage.replace(" km", "").replace(" ", "").strip()
        _fill(page, SELECTORS["field_mileage"], mileage, "przebieg", cb)

        engine = listing.engine_capacity.replace(" cm3", "").replace(" ", "").strip()
        _fill(page, SELECTORS["field_engine"], engine, "pojemność silnika", cb)

        power = listing.engine_power.replace(" KM", "").replace(" ", "").strip()
        _fill(page, SELECTORS["field_power"], power, "moc silnika", cb)

        _select(page, SELECTORS["select_fuel"], _map(listing.fuel_type, FUEL_TYPE_MAP), "paliwo", cb)
        _select(page, SELECTORS["select_gearbox"], _map(listing.gearbox, GEARBOX_MAP), "skrzynia", cb)
        _select(page, SELECTORS["select_body_type"], _map(listing.body_type, BODY_TYPE_MAP), "nadwozie", cb)
        _select(page, SELECTORS["select_condition"], _map(listing.condition, CONDITION_MAP), "stan", cb)

        if listing.color:
            _select(page, SELECTORS["select_color"], listing.color, "kolor", cb)

        _fill(page, SELECTORS["field_price"], listing.price, "cena", cb)

        if listing.description:
            _fill(page, SELECTORS["field_desc"], listing.description, "opis", cb)

        # ── 4. Upload zdjęć ───────────────────────────────────────────────
        if photo_paths:
            file_input = page.query_selector(SELECTORS["photo_input"])
            if file_input:
                _log(f"Wgrywam {len(photo_paths)} zdjęć...", cb)
                try:
                    file_input.set_input_files([str(p) for p in photo_paths])
                    time.sleep(3)
                    _log("Zdjęcia wgrane.", cb)
                except Exception as exc:
                    _log(f"Ostrzeżenie: nie udało się wgrać zdjęć ({exc})", cb)
            else:
                _log("Ostrzeżenie: nie znaleziono pola zdjęć.", cb)

        # ── 5. Wysyłka ───────────────────────────────────────────────────
        _log("Wysyłam ogłoszenie...", cb)
        submit = page.query_selector(SELECTORS["submit"])
        if not submit:
            browser.close()
            return {
                "success": False,
                "message": "Nie znaleziono przycisku wysyłki formularza.",
                "url": None,
            }

        submit.click()
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except PWTimeout:
            pass
        time.sleep(2)

        final_url = page.url
        content = page.content().lower()
        success_kw = ["ogłoszenie dodane", "ogłoszenie zostało", "dziękujemy", "sukces", "dodano"]
        success = any(kw in content for kw in success_kw)

        browser.close()

        if success:
            _log("✅ Ogłoszenie wystawione pomyślnie!", cb)
            return {"success": True, "message": "Ogłoszenie wystawione pomyślnie!", "url": final_url}

        _log("Formularz wysłany – sprawdź konto na Autoplac.pl.", cb)
        return {
            "success": True,
            "message": "Formularz wysłany. Sprawdź konto na Autoplac.pl aby potwierdzić.",
            "url": final_url,
        }
