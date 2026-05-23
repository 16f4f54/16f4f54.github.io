# -*- coding: utf-8 -*-
"""
Uruchom dwuklikiem – otwiera OtoMoto → Autoplac.pl w przeglądarce.
Nie wymaga otwierania terminala.

Wymagania: Python 3.10+ zainstalowany w systemie (python.org)
"""

import os
import sys
import subprocess
import threading
import time
import urllib.request
import urllib.error
import webbrowser
import tkinter as tk
from pathlib import Path

# ── Konfiguracja ─────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
os.chdir(SCRIPT_DIR)

APP_URL       = "http://127.0.0.1:5000"
MARKER_FILE   = SCRIPT_DIR / ".installed"
REQUIREMENTS  = SCRIPT_DIR / "requirements.txt"

# Kolory UI
C_BG     = "#16213e"
C_BG2    = "#1a2744"
C_RED    = "#e94560"
C_GREEN  = "#27ae60"
C_YELLOW = "#e6a817"
C_TEXT   = "#ffffff"
C_MUTED  = "#8899bb"

# ── Interfejs graficzny ───────────────────────────────────────────────────────

class Launcher(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("OtoMoto → Autoplac.pl")
        self.resizable(False, False)
        self.configure(bg=C_BG)
        self.protocol("WM_DELETE_WINDOW", self._zamknij)

        # Wyśrodkuj okno
        szer, wys = 360, 230
        sx = (self.winfo_screenwidth()  - szer) // 2
        sy = (self.winfo_screenheight() - wys)  // 2
        self.geometry(f"{szer}x{wys}+{sx}+{sy}")

        self._proc: subprocess.Popen | None = None
        self._zbuduj_ui()
        threading.Thread(target=self._start, daemon=True).start()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _zbuduj_ui(self):
        # Tytuł
        tk.Label(self, text="OtoMoto → Autoplac.pl",
                 bg=C_BG, fg=C_TEXT,
                 font=("Segoe UI", 15, "bold"),
                 pady=18).pack()

        # Wskaźnik statusu
        row = tk.Frame(self, bg=C_BG)
        row.pack()
        self._dot = tk.Label(row, text="●", fg=C_YELLOW, bg=C_BG,
                              font=("Segoe UI", 16))
        self._dot.pack(side="left", padx=(0, 8))
        self._lbl_status = tk.Label(row, text="Uruchamiam…",
                                     fg=C_MUTED, bg=C_BG,
                                     font=("Segoe UI", 10))
        self._lbl_status.pack(side="left")

        # Drobny log (jedna linijka)
        self._lbl_log = tk.Label(self, text="",
                                  fg=C_MUTED, bg=C_BG,
                                  font=("Segoe UI", 8),
                                  wraplength=340)
        self._lbl_log.pack(pady=(4, 0))

        # Przyciski
        btnf = tk.Frame(self, bg=C_BG, pady=18)
        btnf.pack()

        self._btn_open = tk.Button(
            btnf, text="  Otwórz w przeglądarce  ",
            command=lambda: webbrowser.open(APP_URL),
            bg=C_RED, fg=C_TEXT, activebackground="#c73652",
            font=("Segoe UI", 11, "bold"),
            relief="flat", cursor="hand2",
            padx=18, pady=8, state="disabled",
        )
        self._btn_open.pack(pady=(0, 8))

        self._btn_stop = tk.Button(
            btnf, text="Zatrzymaj i zamknij",
            command=self._zamknij,
            bg=C_BG2, fg=C_MUTED, activebackground="#243050",
            font=("Segoe UI", 9),
            relief="flat", cursor="hand2",
            padx=12, pady=4,
        )
        self._btn_stop.pack()

    def _ustaw_status(self, tekst, kolor_tekstu=C_MUTED, kolor_kropki=C_YELLOW):
        self.after(0, lambda: (
            self._lbl_status.config(text=tekst, fg=kolor_tekstu),
            self._dot.config(fg=kolor_kropki),
        ))

    def _ustaw_log(self, tekst):
        self.after(0, lambda: self._lbl_log.config(text=tekst))

    # ── Logika startowa ──────────────────────────────────────────────────────

    def _start(self):
        """Instalacja (jednorazowo) → start Flask → otwarcie przeglądarki."""

        # ── Krok 1: instalacja zależności ─────────────────────────────────
        if not MARKER_FILE.exists():
            self._ustaw_status("Pierwsze uruchomienie – instaluję pakiety…", C_YELLOW)
            self._ustaw_log("Może potrwać 2–5 minut, proszę czekać…")

            ok = self._uruchom_cicho(
                [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS),
                 "--quiet", "--no-warn-script-location"],
                "pip install"
            )
            if not ok:
                return

            self._ustaw_log("Pobieram przeglądarkę Chromium…")
            ok = self._uruchom_cicho(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                "playwright install"
            )
            if not ok:
                return

            MARKER_FILE.write_text("installed")
            self._ustaw_log("")

        # ── Krok 2: uruchom serwer Flask ──────────────────────────────────
        self._ustaw_status("Uruchamiam serwer…", C_YELLOW)

        flagi = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._proc = subprocess.Popen(
                [sys.executable, str(SCRIPT_DIR / "app.py")],
                cwd=str(SCRIPT_DIR),
                creationflags=flagi,
            )
        except Exception as e:
            self._ustaw_status(f"Błąd: {e}", C_RED, C_RED)
            return

        # ── Krok 3: czekaj aż serwer odpowie ─────────────────────────────
        for i in range(40):   # max 20 sekund
            time.sleep(0.5)
            if self._proc.poll() is not None:  # proces zakończył się sam
                self._ustaw_status("Serwer zatrzymał się nieoczekiwanie", C_RED, C_RED)
                self._ustaw_log("Sprawdź czy nie masz błędu w app.py")
                return
            try:
                urllib.request.urlopen(APP_URL, timeout=1)
                break
            except urllib.error.URLError:
                pass
        else:
            self._ustaw_status("Serwer nie odpowiada", C_RED, C_RED)
            return

        # ── Gotowe ────────────────────────────────────────────────────────
        self._ustaw_status("Aplikacja działa", C_TEXT, C_GREEN)
        self.after(0, lambda: self._btn_open.config(state="normal"))
        webbrowser.open(APP_URL)

    def _uruchom_cicho(self, cmd: list, etykieta: str) -> bool:
        """Uruchamia komendę bez okna konsoli. Zwraca True jeśli OK."""
        try:
            flagi = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            wynik = subprocess.run(
                cmd, cwd=str(SCRIPT_DIR),
                capture_output=True, text=True,
                creationflags=flagi,
            )
            if wynik.returncode != 0:
                blad = (wynik.stderr or wynik.stdout or "nieznany błąd")[:200]
                self._ustaw_status(f"Błąd: {etykieta}", C_RED, C_RED)
                self._ustaw_log(blad)
                return False
            return True
        except Exception as e:
            self._ustaw_status(f"Błąd: {etykieta}", C_RED, C_RED)
            self._ustaw_log(str(e))
            return False

    def _zamknij(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self.destroy()


# ── Start ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    Launcher().mainloop()
