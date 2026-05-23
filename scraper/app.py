"""
Lokalna aplikacja webowa: OtoMoto → Autoplac.pl

Uruchomienie:
    python app.py

Następnie otwórz przeglądarkę: http://localhost:5000
"""

import logging
import os
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, Response, session, url_for
from otomoto_scraper import CarListing, download_photos, scrape_otomoto

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(32))

PHOTO_DIR = Path(os.environ.get("PHOTO_DIR", "static/zdjecia"))
PHOTO_DIR.mkdir(parents=True, exist_ok=True)

# In-memory storage (wystarczy na lokalne użycie jednym użytkownikiem)
_listings: dict[str, CarListing] = {}
_jobs: dict[str, dict] = {}  # job_id → {"status": ..., "logs": [...], "result": {...}}


# ─── Helpers ────────────────────────────────────────────────────────────────

def _listing_to_form(listing: CarListing) -> dict:
    """Konwertuje CarListing na płaski słownik dla szablonu HTML."""
    return {
        "title": listing.title,
        "make": listing.make,
        "model": listing.model,
        "version": listing.version,
        "year": listing.year,
        "mileage": listing.mileage,
        "engine_capacity": listing.engine_capacity,
        "engine_power": listing.engine_power,
        "fuel_type": listing.fuel_type,
        "gearbox": listing.gearbox,
        "body_type": listing.body_type,
        "color": listing.color,
        "doors": listing.doors,
        "seats": listing.seats,
        "condition": listing.condition,
        "price": listing.price,
        "currency": listing.currency,
        "description": listing.description,
        "location_city": listing.location_city,
        "source_url": listing.source_url,
        "photo_count": len(listing.photo_urls),
    }


def _form_to_listing(form: dict, original: CarListing) -> CarListing:
    """Aktualizuje CarListing o wartości z edytowanego formularza."""
    for field in [
        "title", "make", "model", "version", "year", "mileage",
        "engine_capacity", "engine_power", "fuel_type", "gearbox",
        "body_type", "color", "doors", "seats", "condition",
        "price", "currency", "description",
    ]:
        if field in form:
            setattr(original, field, form[field].strip())
    return original


def _run_post_job(job_id: str, listing: CarListing, photo_paths: list, email: str, password: str):
    """Uruchamia post_to_autoplac w osobnym wątku i zapisuje logi do _jobs."""
    from autoplac_poster import post_to_autoplac

    logs = _jobs[job_id]["logs"]

    def append_log(msg: str):
        logs.append(msg)

    try:
        result = post_to_autoplac(
            listing=listing,
            photo_paths=photo_paths,
            email=email,
            password=password,
            headless=True,
            progress_callback=append_log,
        )
    except Exception as exc:
        result = {"success": False, "message": str(exc), "url": None}

    _jobs[job_id]["status"] = "done"
    _jobs[job_id]["result"] = result


# ─── Widoki ─────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/scrape", methods=["POST"])
def scrape():
    url = request.form.get("url", "").strip()
    if not url or "otomoto.pl" not in url:
        return render_template("index.html", error="Podaj prawidłowy link do ogłoszenia OtoMoto.")

    try:
        listing = scrape_otomoto(url)
    except Exception as exc:
        return render_template("index.html", error=f"Błąd scrapingu: {exc}")

    # Pobierz zdjęcia
    listing_id = str(uuid.uuid4())
    photo_dest = PHOTO_DIR / listing_id
    photo_paths = download_photos(listing, photo_dest)
    _listings[listing_id] = listing

    session["listing_id"] = listing_id
    session["photo_paths"] = [str(p) for p in photo_paths]

    return render_template(
        "preview.html",
        listing_id=listing_id,
        form=_listing_to_form(listing),
        photo_paths=[str(p) for p in photo_paths],
        autoplac_email=os.environ.get("AUTOPLAC_EMAIL", ""),
    )


@app.route("/post/<listing_id>", methods=["POST"])
def post(listing_id: str):
    original = _listings.get(listing_id)
    if not original:
        return redirect(url_for("index"))

    # Pobierz edytowane dane z formularza
    listing = _form_to_listing(dict(request.form), original)

    email = request.form.get("autoplac_email", "").strip()
    password = request.form.get("autoplac_password", "").strip()
    photo_paths = [Path(p) for p in session.get("photo_paths", [])]

    if not email or not password:
        return render_template(
            "preview.html",
            listing_id=listing_id,
            form=_listing_to_form(listing),
            photo_paths=session.get("photo_paths", []),
            autoplac_email=email,
            error="Podaj e-mail i hasło do Autoplac.pl.",
        )

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "logs": [], "result": None}

    t = threading.Thread(
        target=_run_post_job,
        args=(job_id, listing, photo_paths, email, password),
        daemon=True,
    )
    t.start()

    return redirect(url_for("progress", job_id=job_id))


@app.route("/progress/<job_id>")
def progress(job_id: str):
    return render_template("progress.html", job_id=job_id)


@app.route("/status/<job_id>")
def status(job_id: str):
    """JSON endpoint pollowany przez stronę postępu."""
    import json
    job = _jobs.get(job_id, {"status": "not_found", "logs": [], "result": None})
    return Response(json.dumps(job), mimetype="application/json")


if __name__ == "__main__":
    print("=" * 50)
    print("  OtoMoto → Autoplac.pl")
    print("  Otwórz: http://localhost:5000")
    print("=" * 50)
    app.run(debug=False, host="127.0.0.1", port=5000)
