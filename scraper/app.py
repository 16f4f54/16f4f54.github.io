"""
Lokalna aplikacja webowa: OtoMoto → Autoplac.pl

Uruchomienie:
    python app.py

Następnie otwórz przeglądarkę: http://localhost:5000
"""

import json
import logging
import os
import sys
import threading
import uuid
from pathlib import Path

# Windows: domyslny codepage (cp1250) nie obsluguje polskich znakow w logach.
# Ustawienie UTF-8 zapobiega UnicodeEncodeError przy starcie.
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, Response, session, url_for
from otomoto_scraper import CarListing, download_photos, scrape_otomoto
import autoplac_poster

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(32))

PHOTO_DIR = Path(os.environ.get("PHOTO_DIR", "static/zdjecia"))
PHOTO_DIR.mkdir(parents=True, exist_ok=True)

_listings: dict[str, CarListing] = {}
_jobs: dict[str, dict] = {}   # job_id → {status, logs, result}


# ─── Helpers ────────────────────────────────────────────────────────────────

def _listing_to_form(listing: CarListing) -> dict:
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
    for field in [
        "title", "make", "model", "version", "year", "mileage",
        "engine_capacity", "engine_power", "fuel_type", "gearbox",
        "body_type", "color", "doors", "seats", "condition",
        "price", "currency", "description",
    ]:
        if field in form:
            setattr(original, field, form[field].strip())
    return original


def _new_job() -> tuple[str, dict]:
    job_id = str(uuid.uuid4())
    job = {"status": "running", "logs": [], "result": None}
    _jobs[job_id] = job
    return job_id, job


def _run_in_thread(fn, job: dict, *args):
    def wrapper():
        logs = job["logs"]
        try:
            result = fn(*args, progress_callback=logs.append)
        except Exception as exc:
            result = {"success": False, "message": str(exc), "url": None}
        job["status"] = "done"
        job["result"] = result
    threading.Thread(target=wrapper, daemon=True).start()


# ─── Widoki: konfiguracja sesji ─────────────────────────────────────────────

@app.route("/setup")
def setup():
    return render_template("setup.html", session_exists=autoplac_poster.session_exists())


@app.route("/setup/login", methods=["POST"])
def setup_login():
    """Startuje widoczną przeglądarkę i czeka na ręczne logowanie."""
    job_id, job = _new_job()
    _run_in_thread(autoplac_poster.save_session, job)
    return redirect(url_for("setup_progress", job_id=job_id, mode="login"))


@app.route("/setup/discover", methods=["POST"])
def setup_discover():
    """Odkrywa pola formularza i zwraca je na stronę."""
    job_id, job = _new_job()
    _run_in_thread(autoplac_poster.discover_form, job)
    return redirect(url_for("setup_progress", job_id=job_id, mode="discover"))


@app.route("/setup/progress/<job_id>")
def setup_progress(job_id: str):
    mode = request.args.get("mode", "login")
    return render_template("progress.html", job_id=job_id, mode=mode, back_url=url_for("setup"))


@app.route("/setup/confirm-login", methods=["POST"])
def confirm_login():
    ok = autoplac_poster.request_save()
    return Response(json.dumps({"ok": ok}), mimetype="application/json")


@app.route("/setup/delete", methods=["POST"])
def setup_delete():
    """Usuwa zapisaną sesję."""
    autoplac_poster.COOKIES_FILE.unlink(missing_ok=True)
    return redirect(url_for("setup"))


# ─── Widoki: główny flow ─────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", session_exists=autoplac_poster.session_exists())


@app.route("/scrape", methods=["POST"])
def scrape():
    url = request.form.get("url", "").strip()
    if not url or "otomoto.pl" not in url:
        return render_template(
            "index.html",
            error="Podaj prawidłowy link do ogłoszenia OtoMoto.",
            session_exists=autoplac_poster.session_exists(),
        )

    try:
        listing = scrape_otomoto(url)
    except Exception as exc:
        return render_template(
            "index.html",
            error=f"Błąd scrapingu: {exc}",
            session_exists=autoplac_poster.session_exists(),
        )

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
        session_exists=autoplac_poster.session_exists(),
    )


@app.route("/post/<listing_id>", methods=["POST"])
def post(listing_id: str):
    original = _listings.get(listing_id)
    if not original:
        return redirect(url_for("index"))

    if not autoplac_poster.session_exists():
        return redirect(url_for("setup"))

    listing = _form_to_listing(dict(request.form), original)
    photo_paths = [Path(p) for p in session.get("photo_paths", [])]

    job_id, job = _new_job()
    logs = job["logs"]

    def run():
        try:
            result = autoplac_poster.post_to_autoplac(
                listing=listing,
                photo_paths=photo_paths,
                progress_callback=logs.append,
            )
        except Exception as exc:
            result = {"success": False, "message": str(exc), "url": None}
        job["status"] = "done"
        job["result"] = result

    threading.Thread(target=run, daemon=True).start()
    return redirect(url_for("progress", job_id=job_id))


@app.route("/progress/<job_id>")
def progress(job_id: str):
    return render_template("progress.html", job_id=job_id, mode="post", back_url=url_for("index"))


@app.route("/status/<job_id>")
def status(job_id: str):
    job = _jobs.get(job_id, {"status": "not_found", "logs": [], "result": None})
    return Response(json.dumps(job), mimetype="application/json")


if __name__ == "__main__":
    print("=" * 50)
    print("  OtoMoto -> Autoplac.pl")
    print("  Otworz: http://localhost:5000")
    if not autoplac_poster.session_exists():
        print("  UWAGA: Brak sesji Autoplac.pl")
        print("  Przejdz do: http://localhost:5000/setup")
    print("=" * 50)
    app.run(debug=False, host="127.0.0.1", port=5000)
