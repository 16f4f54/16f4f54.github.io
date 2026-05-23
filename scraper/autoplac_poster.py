"""
Automatyczne wystawianie ogłoszeń na Autoplac.pl przy użyciu Playwright.

Schemat działania:
  1. Jednorazowo: save_session() → otwiera widoczną przeglądarkę, użytkownik loguje się
     ręcznie, ciasteczka są zapisywane do pliku .autoplac_session.json
  2. Przy każdym ogłoszeniu: post_to_autoplac() wczytuje sesję z pliku – brak potrzeby
     podawania hasła, CAPTCHA nie blokuje, dwuetapowa weryfikacja działa.

UWAGA: Autoplac.pl może zmieniać selektory pól formularza.
Jeśli coś przestanie działać – zaktualizuj słownik SELECTORS.
Wskazówka: DevTools przeglądarki (F12 → Inspector) → kliknij pole → skopiuj selektor.
"""

import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from otomoto_scraper import CarListing

logger = logging.getLogger(__name__)

COOKIES_FILE = Path(".autoplac_session.json")
AUTOPLAC_BASE_URL = "https://www.autoplac.pl"
ADD_LISTING_PATHS = [
    "/dodaj-ogloszenie/",
    "/konto/dodaj/",
    "/ogloszenia/dodaj/",
    "/panel/dodaj-ogloszenie/",
    "/dodaj/",
]

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
# Selektory formularza Autoplac.pl
# Odkryj aktualne selektory przez /setup → "Sprawdź formularz"
# ---------------------------------------------------------------------------

SELECTORS = {
    "field_title":    "input[name='title'], #title, input[placeholder*='tytuł' i]",
    "field_price":    "input[name='price'], #price, input[placeholder*='cena' i]",
    "field_year":     "input[name='year'], input[name='rok'], #year",
    "field_mileage":  "input[name='mileage'], input[name='przebieg'], #mileage",
    "field_engine":   "input[name='engine_capacity'], input[name='pojemnosc'], #engine_capacity",
    "field_power":    "input[name='engine_power'], input[name='moc'], #engine_power",
    "field_desc":     "textarea[name='description'], textarea[name='opis'], #description",
    "select_make":      "select[name='make'], select[name='marka'], #make",
    "select_model":     "select[name='model'], #model",
    "select_fuel":      "select[name='fuel_type'], select[name='paliwo'], #fuel_type",
    "select_gearbox":   "select[name='gearbox'], select[name='skrzynia'], #gearbox",
    "select_body_type": "select[name='body_type'], select[name='nadwozie'], #body_type",
    "select_condition": "select[name='state'], select[name='stan'], #condition",
    "select_color":     "select[name='color'], select[name='kolor'], #color",
    "photo_input":      "input[type='file']",
    "submit":           "button[type='submit']:text('Dodaj ogłoszenie'), button[type='submit']:text('Zapisz'), button[type='submit']",
}

LOGIN_URLS = ["/logowanie/", "/login/", "/konto/logowanie/", "/zaloguj/"]
LOGOUT_SIGNALS = ["a[href*='wyloguj']", "a[href*='logout']", "a:text('Wyloguj')", ".user-menu", "#user-nav"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(msg: str, cb: Optional[Callable] = None) -> None:
    logger.info(msg)
    if cb:
        cb(msg)


def _map(value: str, mapping: dict) -> str:
    return mapping.get(value.strip().lower(), value.strip())


def _make_context(pw, headless: bool = True):
    browser = pw.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    storage = str(COOKIES_FILE) if COOKIES_FILE.exists() else None
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="pl-PL",
        viewport={"width": 1280, "height": 900},
        storage_state=storage,
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, ctx


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
        try:
            page.select_option(selector, label=value)
        except Exception:
            page.select_option(selector, value=value)
        _log(f"  ✓ {label}: {value}", cb)
        return True
    except Exception as exc:
        logger.debug("Błąd select '%s' = '%s': %s", label, value, exc)
        return False


def _is_logged_in(page) -> bool:
    """Sprawdza czy strona wskazuje aktywną sesję."""
    url = page.url.lower()
    if any(k in url for k in ["logowanie", "login", "zaloguj"]):
        return False
    for sel in LOGOUT_SIGNALS:
        try:
            if page.query_selector(sel):
                return True
        except Exception:
            pass
    # Jeśli URL nie zawiera login i nie jesteśmy na stronie głównej to uznajemy za zalogowanych
    return AUTOPLAC_BASE_URL.rstrip("/") != page.url.rstrip("/")


# ---------------------------------------------------------------------------
# Publiczne API
# ---------------------------------------------------------------------------

def session_exists() -> bool:
    return COOKIES_FILE.exists()


def save_session(progress_callback: Optional[Callable[[str], None]] = None) -> dict:
    """
    Otwiera widoczną przeglądarkę i czeka aż użytkownik zaloguje się ręcznie.
    Używa persistent context + usuwa flagi automatyzacji, dzięki czemu
    logowanie przez Google/Facebook działa bez blokad.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "message": "Playwright nie jest zainstalowany."}

    cb = progress_callback
    profile_dir = Path(".browser_profile")
    profile_dir.mkdir(exist_ok=True)

    # Argumenty bez flag wykrywanych przez Google jako bot
    launch_args = dict(
        user_data_dir=str(profile_dir),
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-popup-blocking",   # pozwól na popup Google OAuth
            "--no-default-browser-check",
            "--no-first-run",
        ],
        ignore_default_args=["--enable-automation"],  # to powoduje "debugger wstrzymany"
        locale="pl-PL",
        viewport={"width": 1280, "height": 900},
    )

    with sync_playwright() as pw:
        # Próbuj kolejno: systemowy Chrome → Edge → Playwright Chromium
        ctx = None
        for channel in ["chrome", "msedge", None]:
            try:
                if channel:
                    ctx = pw.chromium.launch_persistent_context(channel=channel, **launch_args)
                    _log(f"Uruchomiono {channel}.", cb)
                else:
                    ctx = pw.chromium.launch_persistent_context(**launch_args)
                    _log("Uruchomiono Chromium.", cb)
                break
            except Exception as e:
                _log(f"Brak {channel or 'Chromium'} ({e}), próbuję dalej...", cb)

        if not ctx:
            return {"success": False, "message": "Nie można uruchomić żadnej przeglądarki."}

        # Każda nowa karta/popup automatycznie wysuwa się na wierzch
        ctx.on("page", lambda p: p.bring_to_front())

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(AUTOPLAC_BASE_URL + LOGIN_URLS[0], wait_until="domcontentloaded", timeout=30_000)
        page.bring_to_front()
        _log("Przeglądarka otwarta. Zaloguj się (e-mail, Google, itp.) – skrypt sam wykryje kiedy skończyłeś.", cb)

        # Czekaj do 5 minut – sprawdzaj wszystkie otwarte karty
        for i in range(300):
            time.sleep(1)
            logged_in = False
            for p in list(ctx.pages):
                try:
                    if AUTOPLAC_BASE_URL in p.url and _is_logged_in(p):
                        logged_in = True
                        break
                except Exception:
                    pass
            if logged_in:
                _log("Wykryto zalogowanie!", cb)
                break
            if i > 0 and i % 30 == 0:
                _log(f"Wciąż czekam... ({i}s). Zaloguj się w przeglądarce.", cb)
        else:
            ctx.close()
            return {"success": False, "message": "Przekroczono limit czasu (5 min). Spróbuj ponownie."}

        ctx.storage_state(path=str(COOKIES_FILE))
        ctx.close()
        _log("Sesja zapisana. Możesz teraz wystawiać ogłoszenia.", cb)
        return {"success": True, "message": "Sesja zapisana pomyślnie."}


def discover_form(progress_callback: Optional[Callable[[str], None]] = None) -> dict:
    """
    Wczytuje zapisaną sesję, wchodzi na formularz dodawania ogłoszenia
    i zwraca listę znalezionych pól. Służy do weryfikacji/aktualizacji SELECTORS.
    """
    if not session_exists():
        return {"success": False, "message": "Brak zapisanej sesji. Najpierw się zaloguj.", "fields": []}

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return {"success": False, "message": "Playwright nie jest zainstalowany.", "fields": []}

    cb = progress_callback
    _log("Wczytuję sesję i szukam formularza...", cb)

    with sync_playwright() as pw:
        browser, ctx = _make_context(pw, headless=True)
        page = ctx.new_page()

        form_url = None
        for path in ADD_LISTING_PATHS:
            url = AUTOPLAC_BASE_URL + path
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            except PWTimeout:
                continue
            time.sleep(0.8)

            # Sprawdź czy jesteśmy na formularzu (a nie przekierowani na logowanie)
            if any(k in page.url for k in ["logowanie", "login"]):
                browser.close()
                return {"success": False, "message": "Sesja wygasła – zaloguj się ponownie.", "fields": []}

            inputs = page.query_selector_all("input:not([type='hidden']):not([type='submit'])")
            selects = page.query_selector_all("select")
            textareas = page.query_selector_all("textarea")

            if inputs or selects or textareas:
                form_url = url
                break

        if not form_url:
            browser.close()
            return {
                "success": False,
                "message": "Nie znaleziono formularza. Sprawdź ADD_LISTING_PATHS w autoplac_poster.py.",
                "fields": [],
            }

        _log(f"Formularz na: {form_url}", cb)

        # Wyciągnij informacje o wszystkich polach
        fields = []

        for el in page.query_selector_all("input:not([type='hidden']):not([type='submit']):not([type='button'])"):
            try:
                fields.append({
                    "tag": "input",
                    "type": el.get_attribute("type") or "text",
                    "name": el.get_attribute("name") or "",
                    "id": el.get_attribute("id") or "",
                    "placeholder": el.get_attribute("placeholder") or "",
                })
            except Exception:
                pass

        for el in page.query_selector_all("select"):
            try:
                options = [
                    o.get_attribute("value") or o.inner_text()
                    for o in el.query_selector_all("option")
                ]
                fields.append({
                    "tag": "select",
                    "type": "select",
                    "name": el.get_attribute("name") or "",
                    "id": el.get_attribute("id") or "",
                    "placeholder": "",
                    "options": options[:20],  # max 20 opcji żeby nie zaśmiecać
                })
            except Exception:
                pass

        for el in page.query_selector_all("textarea"):
            try:
                fields.append({
                    "tag": "textarea",
                    "type": "textarea",
                    "name": el.get_attribute("name") or "",
                    "id": el.get_attribute("id") or "",
                    "placeholder": el.get_attribute("placeholder") or "",
                })
            except Exception:
                pass

        browser.close()
        _log(f"Znaleziono {len(fields)} pól formularza.", cb)
        return {"success": True, "fields": fields, "form_url": form_url}


def post_to_autoplac(
    listing: CarListing,
    photo_paths: list[Path],
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Wystawia ogłoszenie na Autoplac.pl używając zapisanej sesji.

    Przed pierwszym użyciem uruchom save_session() (przez /setup w aplikacji).

    Returns:
        {"success": bool, "message": str, "url": str|None}
    """
    cb = progress_callback

    if not session_exists():
        return {
            "success": False,
            "message": "Brak zapisanej sesji Autoplac.pl. Otwórz /setup i zaloguj się.",
            "url": None,
        }

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return {
            "success": False,
            "message": "Playwright nie jest zainstalowany.\nUruchom: pip install playwright && playwright install chromium",
            "url": None,
        }

    with sync_playwright() as pw:
        browser, ctx = _make_context(pw, headless=True)
        page = ctx.new_page()

        # ── 1. Znajdź formularz ──────────────────────────────────────────
        _log("Szukam formularza dodawania ogłoszenia...", cb)
        form_found = False
        for path in ADD_LISTING_PATHS:
            url = AUTOPLAC_BASE_URL + path
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            except PWTimeout:
                continue
            time.sleep(0.8)

            if any(k in page.url for k in ["logowanie", "login"]):
                browser.close()
                return {
                    "success": False,
                    "message": "Sesja wygasła. Otwórz /setup i zaloguj się ponownie.",
                    "url": None,
                }

            if (page.query_selector(SELECTORS["field_title"])
                    or page.query_selector(SELECTORS["field_price"])):
                form_found = True
                _log(f"Formularz: {url}", cb)
                break

        if not form_found:
            browser.close()
            return {
                "success": False,
                "message": (
                    "Nie znaleziono formularza.\n"
                    "Użyj /setup → 'Sprawdź formularz' aby zobaczyć dostępne pola."
                ),
                "url": None,
            }

        # ── 2. Wypełnianie danych ────────────────────────────────────────
        _log("Wypełniam dane ogłoszenia...", cb)

        _fill(page, SELECTORS["field_title"], listing.title, "tytuł", cb)

        if listing.make and _select(page, SELECTORS["select_make"], listing.make, "marka", cb):
            time.sleep(1)  # modele ładowane dynamicznie po wyborze marki
        if listing.model:
            _select(page, SELECTORS["select_model"], listing.model, "model", cb)

        _fill(page, SELECTORS["field_year"], listing.year, "rok", cb)

        mileage = listing.mileage.replace(" km", "").replace(" ", "").replace(" ", "").strip()
        _fill(page, SELECTORS["field_mileage"], mileage, "przebieg", cb)

        engine = listing.engine_capacity.replace(" cm3", "").replace(" ", "").replace(" ", "").strip()
        _fill(page, SELECTORS["field_engine"], engine, "pojemność silnika", cb)

        power = listing.engine_power.replace(" KM", "").replace(" ", "").replace(" ", "").strip()
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

        # ── 3. Upload zdjęć ──────────────────────────────────────────────
        if photo_paths:
            file_input = page.query_selector(SELECTORS["photo_input"])
            if file_input:
                _log(f"Wgrywam {len(photo_paths)} zdjęć...", cb)
                try:
                    file_input.set_input_files([str(p) for p in photo_paths if Path(p).exists()])
                    time.sleep(3)
                    _log("Zdjęcia wgrane.", cb)
                except Exception as exc:
                    _log(f"Ostrzeżenie: nie udało się wgrać zdjęć ({exc})", cb)
            else:
                _log("Ostrzeżenie: nie znaleziono pola zdjęć (pole input[type=file]).", cb)

        # ── 4. Wysyłka ───────────────────────────────────────────────────
        _log("Wysyłam ogłoszenie...", cb)
        submit = page.query_selector(SELECTORS["submit"])
        if not submit:
            browser.close()
            return {
                "success": False,
                "message": "Nie znaleziono przycisku 'Dodaj ogłoszenie'. Zaktualizuj SELECTORS['submit'].",
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
        success_kw = ["ogłoszenie dodane", "ogłoszenie zostało", "dziękujemy", "sukces", "dodano", "wystawione"]
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
