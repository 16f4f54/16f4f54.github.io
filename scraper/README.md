# OtoMoto → Autoplac.pl

Lokalny skrypt dla komisu: wystarczy wkleić link do ogłoszenia z OtoMoto,
a dane (parametry, opis, zdjęcia) trafią automatycznie na Autoplac.pl.

## Instalacja (jednorazowo)

Potrzebujesz Pythona 3.10 lub nowszego.

```bash
# Przejdź do folderu skryptu
cd scraper

# Zainstaluj zależności
pip install -r requirements.txt

# Zainstaluj przeglądarkę Chromium dla Playwright
playwright install chromium
```

## Konfiguracja (opcjonalna)

Skopiuj plik z przykładową konfiguracją:

```bash
cp .env.example .env
```

Otwórz `.env` i wpisz e-mail do Autoplac.pl (hasło można podać też w formularzu, nie trzeba go tu zapisywać).

## Uruchomienie

```bash
python app.py
```

Następnie otwórz w przeglądarce: **http://localhost:5000**

## Pierwsze uruchomienie – jednorazowe logowanie

Skrypt **nie przechowuje hasła**. Zamiast tego:

1. Wejdź na http://localhost:5000/setup
2. Kliknij **"Otwórz przeglądarkę i zaloguj się"**
3. Zaloguj się normalnie na Autoplac.pl w otwartym oknie Chromium
4. Skrypt wykryje logowanie i zapisze sesję do pliku `.autoplac_session.json`

Sesja jest ważna zazwyczaj kilka tygodni. Gdy wygaśnie – powtórz krok 2-3.

## Użycie krok po kroku (od drugiego razu)

1. Wklej link do ogłoszenia z OtoMoto (np. `https://www.otomoto.pl/osobowe/oferta/ford-focus-...html`)
2. Kliknij **Pobierz dane** – skrypt ściągnie wszystkie informacje i zdjęcia
3. Sprawdź / popraw pobrane dane w formularzu
4. Kliknij **Wyślij na Autoplac.pl** – skrypt automatycznie wypełni formularz i wyśle ogłoszenie

## Rozwiązywanie problemów

| Problem | Rozwiązanie |
|---------|-------------|
| Dane nie zostały pobrane z OtoMoto | OtoMoto mogło zmienić strukturę strony – zgłoś, zaktualizujemy scraper |
| Pole X nie wypełnia się na Autoplac | Autoplac zmienił formularz – zaktualizuj `SELECTORS` w `autoplac_poster.py` |
| Błąd logowania | Sprawdź e-mail i hasło do Autoplac.pl |
| Błąd "Playwright nie zainstalowany" | Uruchom: `playwright install chromium` |

## Wymagania

- Python 3.10+
- Aktywne konto na Autoplac.pl
- Połączenie z internetem
