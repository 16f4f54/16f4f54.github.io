"""
Scraper ogłoszeń z OtoMoto.pl.

Wyciąga dane z osadzonego bloku __NEXT_DATA__ (Next.js),
co jest stabilniejsze niż parsowanie HTML i nie wymaga Selenium.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Referer": "https://www.otomoto.pl/",
}


@dataclass
class CarListing:
    """Ujednolicona reprezentacja ogłoszenia samochodu."""

    title: str = ""
    make: str = ""
    model: str = ""
    version: str = ""
    year: str = ""
    mileage: str = ""          # km (samo liczba)
    engine_capacity: str = ""  # cm3 (samo liczba)
    engine_power: str = ""     # KM (samo liczba)
    fuel_type: str = ""
    gearbox: str = ""
    body_type: str = ""
    color: str = ""
    doors: str = ""
    seats: str = ""
    country_origin: str = ""
    condition: str = ""        # "Używany" / "Nowy"
    price: str = ""            # samo liczba
    currency: str = "PLN"
    description: str = ""
    photo_urls: list = field(default_factory=list)
    location_city: str = ""
    location_region: str = ""
    source_url: str = ""
    extra_params: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"Tytuł:        {self.title}",
            f"Marka/Model:  {self.make} {self.model} {self.version}".strip(),
            f"Rok:          {self.year}",
            f"Przebieg:     {self.mileage} km",
            f"Silnik:       {self.engine_capacity} cm³ / {self.engine_power} KM",
            f"Paliwo:       {self.fuel_type}",
            f"Skrzynia:     {self.gearbox}",
            f"Nadwozie:     {self.body_type}",
            f"Stan:         {self.condition}",
            f"Cena:         {self.price} {self.currency}",
            f"Miasto:       {self.location_city} ({self.location_region})",
            f"Zdjęcia:      {len(self.photo_urls)} szt.",
        ]
        return "\n".join(lines)


def _extract_next_data(soup: BeautifulSoup) -> dict:
    """Wyciąga i parsuje blok __NEXT_DATA__ ze strony Next.js."""
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        raise ValueError("Brak bloku __NEXT_DATA__ na stronie – OtoMoto mogło zmienić strukturę.")
    return json.loads(script.string)


def _find_ad_node(next_data: dict) -> dict:
    """
    Szuka węzła ogłoszenia w __NEXT_DATA__.
    OtoMoto kilkakrotnie zmieniało strukturę, stąd wiele ścieżek.
    """
    candidates = [
        ["props", "pageProps", "ad"],
        ["props", "pageProps", "advert"],
        ["props", "pageProps", "data", "ad"],
        ["props", "pageProps", "data", "advert"],
        ["props", "pageProps", "initialState", "ad"],
    ]
    for path in candidates:
        node = next_data
        try:
            for key in path:
                node = node[key]
            if node and isinstance(node, dict) and "title" in node:
                logger.debug("Dane ogłoszenia znalezione w: %s", ".".join(path))
                return node
        except (KeyError, TypeError):
            continue

    # Ostatnia szansa: szukaj klucza 'title' z 'parameters' gdziekolwiek w drzewie
    def _deep_search(obj, depth=0):
        if depth > 6 or not isinstance(obj, dict):
            return None
        if "title" in obj and "parameters" in obj:
            return obj
        for v in obj.values():
            result = _deep_search(v, depth + 1)
            if result:
                return result
        return None

    found = _deep_search(next_data)
    if found:
        return found

    raise ValueError(
        "Nie znaleziono danych ogłoszenia w __NEXT_DATA__. "
        "OtoMoto prawdopodobnie zmieniło strukturę strony."
    )


def _parse_price(price_node) -> tuple[str, str]:
    """Zwraca (kwota_string, waluta)."""
    if not price_node or not isinstance(price_node, dict):
        return "", "PLN"
    # Nowa struktura: price.regularPrice.amount / price.amount.units
    for path in [
        ["regularPrice", "amount"],
        ["amount", "units"],
        ["amount"],
    ]:
        node = price_node
        try:
            for k in path:
                node = node[k]
            if node is not None:
                currency = (
                    price_node.get("currency")
                    or price_node.get("amount", {}).get("currencyCode", "PLN")
                    or "PLN"
                )
                return str(int(node)), currency
        except (KeyError, TypeError, ValueError):
            continue
    return "", "PLN"


def _parse_parameters(params: list) -> dict:
    """
    Konwertuje listę parametrów OtoMoto na słownik.
    Każdy element to {"key": "year", "displayValue": "2020", "value": "2020"}.
    """
    result = {}
    for p in params:
        if not isinstance(p, dict):
            continue
        key = p.get("key", "")
        # displayValue jest czytelne dla człowieka; value to wewnętrzny identyfikator
        value = p.get("displayValue") or p.get("value") or ""
        result[key] = str(value)
    return result


def _best_photo_url(url: str) -> str:
    """Zamienia rozmiar miniatury na możliwie największy."""
    url = re.sub(r";s=\d+x\d+", ";s=1920x1440", url)
    url = re.sub(r"width=\d+", "width=1920", url)
    return url


def download_photos(listing: CarListing, dest_dir: Path) -> list[Path]:
    """
    Pobiera zdjęcia z ogłoszenia OtoMoto do podanego katalogu.
    Zwraca listę ścieżek do pobranych plików.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    session = requests.Session()
    session.headers.update(HEADERS)

    for idx, url in enumerate(listing.photo_urls, start=1):
        ext = Path(urlparse(url).path).suffix or ".jpg"
        dest = dest_dir / f"foto_{idx:02d}{ext}"
        if dest.exists():
            logger.info("Zdjęcie %s już istnieje, pomijam.", dest.name)
            paths.append(dest)
            continue
        try:
            resp = session.get(url, timeout=30, stream=True)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            logger.info("Pobrano zdjęcie %d/%d → %s", idx, len(listing.photo_urls), dest.name)
            paths.append(dest)
            time.sleep(0.3)
        except Exception as exc:
            logger.warning("Nie udało się pobrać zdjęcia %s: %s", url, exc)

    return paths


def scrape_otomoto(url: str) -> CarListing:
    """
    Główna funkcja scrapera.

    Pobiera stronę ogłoszenia OtoMoto i zwraca wypełniony obiekt CarListing.
    Przy błędzie rzuca ValueError lub requests.HTTPError.
    """
    logger.info("Pobieram ogłoszenie: %s", url)
    session = requests.Session()
    session.headers.update(HEADERS)

    resp = session.get(url, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    next_data = _extract_next_data(soup)
    ad = _find_ad_node(next_data)

    listing = CarListing(source_url=url)
    listing.title = ad.get("title", "").strip()

    # Cena
    listing.price, listing.currency = _parse_price(ad.get("price"))

    # Parametry techniczne
    raw_params = _parse_parameters(ad.get("parameters", []))

    PARAM_MAP = {
        "make":             "make",
        "model":            "model",
        "version":          "version",
        "year":             "year",
        "mileage":          "mileage",
        "engine_capacity":  "engine_capacity",
        "engine_power":     "engine_power",
        "fuel_type":        "fuel_type",
        "gearbox":          "gearbox",
        "body_type":        "body_type",
        "color":            "color",
        "no_door":          "doors",
        "nr_seats":         "seats",
        "country_origin":   "country_origin",
        "state":            "condition",
    }

    for otomoto_key, field_name in PARAM_MAP.items():
        if otomoto_key in raw_params:
            setattr(listing, field_name, raw_params.pop(otomoto_key))

    listing.extra_params = raw_params

    # Opis
    desc = ad.get("description", {})
    if isinstance(desc, dict):
        listing.description = desc.get("value", "").strip()
    elif isinstance(desc, str):
        listing.description = desc.strip()

    # Zdjęcia
    for photo in ad.get("photos", []):
        if isinstance(photo, dict):
            photo_url = photo.get("url") or photo.get("link") or ""
        elif isinstance(photo, str):
            photo_url = photo
        else:
            continue
        if photo_url:
            listing.photo_urls.append(_best_photo_url(photo_url))

    # Lokalizacja
    loc = ad.get("location", {})
    if isinstance(loc, dict):
        city = loc.get("city", {})
        region = loc.get("region", {})
        listing.location_city = (city.get("name") if isinstance(city, dict) else city) or ""
        listing.location_region = (region.get("name") if isinstance(region, dict) else region) or ""

    # Wypełnij make/model z tytułu jeśli brakuje
    if not listing.make and listing.title:
        parts = listing.title.split()
        if parts:
            listing.make = parts[0]
        if len(parts) > 1:
            listing.model = parts[1]

    logger.info("Dane ogłoszenia:\n%s", listing.summary())
    return listing
