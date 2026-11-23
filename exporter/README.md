# CS2 Save Exporter & Reddit Collector (`exporter/`)

Samodzielne narzędzie wsadowe do masowego konwertowania plików zapisu **Cities: Skylines II (`.cok`)** na:
- **Interaktywne mapy HTML** (w stylu Google Maps),
- **Standaryzowane zbiory danych GeoJSON** zgodne w 100% z kontraktami i schematami JSON projektu **Koru** ([Tensor-PK/Koru](https://github.com/Tensor-PK/Koru/tree/main/contracts/schemas)),
- **Pliki CSV** z lokalizacją i parametrami wszystkich firm/biznesów w mieście,
- Wraz z opcjonalnym kolektorem save'ów z `r/CitiesSkylines2`.

Folder `exporter/` zawiera wszystkie potrzebne moduły (`cs2save.py`, `cok_to_html.py`, `schemas/`, `lib/`) i można go przenieść na dowolny dysk.

---

## Struktura folderów

```text
exporter/
├── exporter.py                  # główny eksporter wsadowy (.cok -> .html, .geojson, _biznesy.csv)
├── run_exporter.bat             # skrót uruchamiający exporter.py (Windows)
├── reddit_scraper.py            # kolektor save'ów z r/CitiesSkylines2 (oficjalne Reddit OAuth2 API)
├── run_scraper.bat              # skrót uruchamiający reddit_scraper.py na 4h
├── reddit_config.example.json   # szablon konfiguracji kluczy Reddit API
├── cs2save.py                   # dekoder binarnego formatu .cok
├── cok_to_html.py               # silnik ekstrakcji, eksportu HTML, GeoJSON i CSV
├── schemas/                     # lokalne schematy JSON kontraktów Koru (dataset, feature, crs, geometry, common)
├── lib/                         # lokalny Leaflet CSS/JS (mapy działają 100% offline)
│
├── to_export/                   # <- TU WRZUCASZ nowe pliki .cok do przetworzenia
├── already_exported/            # <- tu automatycznie trafiają poprawnie wyeksportowane .cok
├── exported_htmls/              # <- tu powstają gotowe mapy <Miasto>.html i <Miasto>_biznesy.csv
├── exported_geojson/            # <- tu powstają zbiory danych <Miasto>.geojson (kontrakt Koru)
└── export_error/                # <- tu trafiają uszkodzone/błędne .cok + raport "export error.txt"
```

---

## 1. Masowy eksport save'ów (`exporter.py`)

Wrzuć jeden lub wiele plików `.cok` do `to_export/` i uruchom:

```powershell
python exporter.py
```

lub kliknij dwukrotnie `run_exporter.bat`.

### Co się wydarzy dla każdego pliku `.cok`
1. Powstaje zbiór danych GeoJSON `exported_geojson/<Nazwa>.geojson` zgodny w 100% ze schematem `dataset.schema.json` Koru (gotowy do bezpośredniego zasilania potoku Koru).
2. Powstaje samodzielna, interaktywna mapa `exported_htmls/<Nazwa>.html` (otwierana lokalnie w przeglądarce bez CORS).
3. Powstaje tabela wszystkich aktywnych firm `exported_htmls/<Nazwa>_biznesy.csv` (kodowanie UTF-8 z BOM — polskie znaki działają od razu w Excelu i `pandas`).
4. Plik źródłowy `.cok` zostaje przeniesiony z `to_export/` do `already_exported/`.
5. **W razie błędu**: problematyczny `.cok` trafia do `export_error/`, a dokładny opis przyczyny i traceback zapisują się w `export_error/export error.txt`. Eksporter przechodzi dalej do kolejnego pliku.

### Opcje uruchamiania i wyłączania formatów
```powershell
# Wyłączenie generowania map HTML (szybszy bieg, tylko GeoJSON i CSV):
python exporter.py --no-html          # lub --nohtml

# Wyłączenie generowania tabel CSV:
python exporter.py --no-csv           # lub --nocsv

# Wyłączenie generowania GeoJSON:
python exporter.py --no-geojson       # lub --nogeojson

# Generowanie tylko GeoJSON (bez HTML i bez CSV):
python exporter.py --no-html --no-csv

# Układ współrzędnych w GeoJSON:
python exporter.py --crs geographic   # (domyślny) WGS84 EPSG:4326
python exporter.py --crs local        # lokalny układ metryczny gry CS2 (metry, x-east-y-north)

# Przeliczenie ponownie save'ów już znajdujących się w already_exported/:
python exporter.py --regen-all

# Pominięcie generowania podkładu PNG terenu/wody dla map HTML (szybsze):
python exporter.py --no-terrain
```

---

## 2. Eksport do Koru GeoJSON (`exported_geojson/`)

Generowany plik `.geojson` to kanoniczny GeoJSON FeatureCollection zgodny ze specyfikacją kontraktów Koru (`contracts/schemas`):
- `dataset.schema.json`: zawiera metadane w sekcji `koru` (`koru_version: "0.1.0"`, `id`, `source`, `crs`, `bbox`, `generated_at`, `generator`).
- `feature.schema.json`: każdy obiekt mapy posiada ściśle określoną taksonomię `class` (`road`, `railway`, `building`, `water`, `boundary`, `poi`, `unclassified`), `subclass`, unikalne `id`, `source_ref` oraz znormalizowane `attributes` (tylko klucze `snake_case`).
- `geometry.schema.json`: pełne wsparcie dla `LineString` (drogi, tory, trasy komunikacji miejskiej, media), `Polygon` (obrysy budynków, dzielnice) oraz `Point` (usługi POI, przystanki, przedsiębiorstwa).
- Automatyczna weryfikacja schematu: każdorazowo podczas eksportu następuje walidacja poprawności danych za pomocą `jsonschema` względem definicji w `schemas/dataset.schema.json`.

---

## 2. Eksport biznesów i firm (do analizy)

Dla każdego miasta eksportowane są wszystkie aktywne firmy (`Game.Companies.CompanyData`) powiązane z budynkiem i dzielnicą:

- **Na mapie HTML**:
  - Warstwa **🏪 Biznesy i Firmy** (kolory według sektora: Handel, Biura, Przemysł, Magazyny, Wydobycie).
  - Kliknięcie kropki firmy lub budynku pokazuje markę, branżę, zatrudnienie, rentowność, kapitał i czynsz.
  - Wyszukiwarka z filtrem `🏪 Biznesy` (można szukać po marce lub branży, np. `Bank`, `Restaurant`, `Software`, `Electronics`).
  - Zakładka **🏪 Top branże** w oknie statystyk miasta (`ℹ️`).
- **W pliku `exported_htmls/<Miasto>_biznesy.csv`**:

| Kolumna | Opis |
| :--- | :--- |
| `city` | Nazwa miasta z metadanych save'a |
| `brand` | Marka / nazwa firmy (`BrandPrefab` lub własna nazwa z gry) |
| `sector` | Sektor: `Commercial`, `Office`, `Industrial`, `Warehouse`, `Extractor` |
| `business_type` | Czytelna branża (np. `Gas Station`, `Software Company`, `Electronics Factory`) |
| `prefab` | Oryginalny prefab CS2 (np. `Commercial_Restaurant`, `Office_Bank`) |
| `district` | Nazwa dzielnicy |
| `building` / `building_level` | Nazwa i poziom budynku (1–5) |
| `employees` / `max_employees` | Aktualne zatrudnienie / maksymalna liczba etatów |
| `profitability` | Rentowność firmy (`0–255`) |
| `worth` / `rent` | Wartość/kapitał firmy oraz czynsz budynku (`₡`) |
| `x_m` / `z_m` | Współrzędne lokalne w metrach CS2 (`[-7168, +7168]`) |
| `lat` / `lon` | Współrzędne geograficzne zgodne z mapą HTML |

Przykład szybkiej analizy w Pythonie:
```python
import pandas as pd
df = pd.read_csv("exported_htmls/Ożarów Mazowiecki_biznesy.csv")
print(df.groupby(["district", "sector"])["employees"].agg(["count", "sum"]))
```

---

## 3. Zbieranie save'ów z Reddita (`reddit_scraper.py`)

Skrypt przeszukuje `r/CitiesSkylines2` (posty + komentarze), pobiera wyłącznie zweryfikowane pliki `.cok` (lub wypakowuje `.cok` z archiwów `.zip`) i wrzuca je prosto do `to_export/`.

### Konfiguracja Reddit OAuth2 API (darmowe, ~30 s)
1. Wejdź na https://www.reddit.com/prefs/apps → **create another app...** → typ **script**.
2. Jako `redirect uri` wpisz np. `http://localhost:8080` i zatwierdź.
3. Skopiuj `reddit_config.example.json` jako `reddit_config.json` i wklej `client_id` oraz `client_secret` (można też podać przez zmienne środowiskowe `REDDIT_CLIENT_ID` i `REDDIT_CLIENT_SECRET`).

### Uruchomienie
```powershell
# Zbieranie przez 4 godziny do to_export/ (później uruchom exporter.py):
python reddit_scraper.py --hours 4

# Zbieranie przez 6 godzin + automatyczny eksport każdego pobranego save'a:
python reddit_scraper.py --hours 6 --auto-export

# Skanowanie kilku subredditów z bezpiecznym limitem wolnego miejsca na dysku:
python reddit_scraper.py --subs CitiesSkylines2 CitiesSkylines --hours 4 --min-free-mb 1000
```

### Jakie linki obsługuje i jak dba o bezpieczeństwo
- **Pobieranie automatyczne**: bezpośrednie `.cok`/`.zip` oraz publiczne linki **Google Drive** i **Dropbox**.
- **Linki ręczne**: linki z serwisów wymagających przeglądarki (Mega, MediaFire, Paradox Mods, Gofile, OneDrive) zapisywane są do `manual_links.txt`.
- **Walidacja**: każdy pobrany plik musi mieć nagłówek ZIP (`PK\x03\x04`) i zawierać `*.SaveGameData` (strony HTML / logowania / obrazy są natychmiast odrzucane).
- **Deduplikacja**: odwiedzone posty, URL-e i sumy SHA-256 zapisują się w `scraper_state.json`.
- **Ochrona dysku**: parametr `--min-free-mb` (domyślnie `500` MB) zatrzyma pobieranie, zanim zapełni dysk, a `--max-size-mb` (domyślnie `500` MB) pomija zbyt duże archiwa.
