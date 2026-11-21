# Cities: Skylines II — Odczyt Save (`.cok`) i Interaktywna Mapa HTML

Projekt inżynierii wstecznej formatu zapisu gry **Cities: Skylines II (`.cok`)** w czystym Pythonie (bez uruchamiania gry i bez bibliotek DLL z silnika gry). Dekoduje binarny świat Unity DOTS/ECS prosto z pliku `.cok` i generuje samodzielną, interaktywną mapę miasta w stylu Google Maps (`.html`) oraz tabelę firm/biznesów (`.csv`).

---

## Najważniejsze możliwości

- **100% czysty Python 3.14+ / 3.15+** — zero zewnętrznych zależności (`zipfile`, `struct`, `zlib`, `json`, `csv`, `compression.zstd`).
- **Bezpośredni dekoder `.cok` ([cs2save.py](cs2save.py))**:
  - Rozpakowuje kontener ZIP (`.SaveGameMetadata`, `.SaveGameData`, `.Prefab`) i strumień bloków **Zstandard**.
  - Parsuje tabelę 800+ typów komponentów ECS, tabelę systemów i wszystkie archetypy co do bajtu.
  - Obsługuje kodowanie flagi `32` (`decode32`: delta bajtowa + rozplatanie płaszczyzn bajtów), flagi `36` (bufory zmiennodługościowe np. wielokąty dzielnic), enableable tagi (`flag & 16`), nazwy z polskimi znakami z `Game.UI.NameSystem` oraz zmiennodługościowy `Pathfind.PathElement`.
- **Konwerter `.cok` → HTML + CSV ([cok_to_html.py](cok_to_html.py))**:
  - **Teren i woda**: renderuje rzeczywistą linię brzegową, rzeki, zatoki i relief wysokości z `TerrainSystem` (`4096×4096`) i `WaterSystem` (`2048×2048`) jako wbudowane obrazy PNG (tryb jasny i nocny).
  - **Sieć drogowa i kolejowa**: krzywe Béziera 3. stopnia (`Net.Curve`), nazwy ulic (`Net.AggregateElement`), limity prędkości (`RoadData`), mosty i tunele (`Net.Elevation`), autostrady, aleje, ulice, deptaki, drogi rowerowe, tory kolejowe, metro, tramwaje, pasy startowe i opcjonalnie media (prąd/rury).
  - **Budynki**: dokładne obrócone obrysy (`Objects.Transform` + `ObjectGeometryData`), strefy, poziomy rozwoju, gospodarstwa domowe, mieszkańcy i miejsca pracy.
  - **Biznesy i firmy**: wszystkie aktywne firmy (handel, biura, przemysł, magazyny, wydobycie) z marką (`BrandPrefab`), branżą (`CompanyPrefab`), zatrudnieniem (`employees / max_employees`), rentownością, kapitałem, czynszem, dzielnicą i dokładnymi współrzędnymi — zarówno jako warstwa na mapie, jak i plik `.csv` do analizy w Excelu/Pandas.
  - **Komunikacja miejska**: pełne trasy po pasach (`PathElement → Net.Curve`) dla autobusów, tramwajów, metra, pociągów, promów/statków i samolotów, z przystankami, pojazdami i dokładną liczbą pasażerów (włącznie ze składami wielowagonowymi przez `LayoutElement`).
  - **Dzielnice i kafelki mapy**: granice dzielnic, statystyki demograficzne oraz siatka 23×23 kafelków gry (odblokowane vs zablokowane).
  - **Samodzielny plik HTML**: Leaflet, dane miasta i warstwy PNG są wbudowane w jeden plik `.html` — otwiera się natychmiast po dwukliku (zero problemów z CORS, działa offline).

---

## Struktura repozytorium

| Plik / Folder | Opis |
| :--- | :--- |
| **`cs2save.py`** | Rdzeń dekodera plików `.cok` (ZIP + Zstd + ECS + systemy). |
| **`cok_to_html.py`** | Generator pojedynczego pliku HTML (i opcjonalnie CSV biznesów) z pliku `.cok`. |
| **`exporter/`** | Samodzielny podprojekt do masowego eksportu save'ów i zbierania ich z Reddita — zobacz **[exporter/README.md](exporter/README.md)**. |
| **`ozarow_z_cok.html`** | Przykładowa wygenerowana mapa Ożarowa Mazowieckiego bezpośrednio z `.cok`. |
| **`mapa_ozarow_cs2.html`** / `prepare_map_data.py` | Wcześniejsza mapa referencyjna zbudowana z eksportu GIS moda **Carto** (użyta jako *Ground Truth* do weryfikacji). |
| **`AGENTS.md`** | Dokumentacja techniczna formatu binarnego `.cok` oraz rejestr napotkanych pułapek. |

---

## Szybki start (pojedynczy plik `.cok`)

```powershell
# Eksport samego HTML:
python cok_to_html.py "Miasto.cok" -o miasto.html

# Eksport HTML + tabela biznesów CSV (Excel / pandas):
python cok_to_html.py "Miasto.cok" -o miasto.html --csv miasto_biznesy.csv

# Szybszy eksport bez warstwy terenu/wody:
python cok_to_html.py "Miasto.cok" -o miasto.html --no-terrain
```

Do przetwarzania wielu save'ów naraz i automatycznego pobierania z Reddita użyj podprojektu **[exporter/](exporter/README.md)**.

---

## Walidacja dokładności (vs Carto Ground Truth)

Zweryfikowane na zapisie *Ożarów Mazowiecki.cok* (131 MB, 2 585 242 encji):

| Metryka | Bezpośrednio z `.cok` | Carto (eksport z gry) | Zgodność |
| :--- | :---: | :---: | :---: |
| Mieszkańcy | 339 311 | 339 314 | 99.999% |
| Miejsca pracy | 67 013 | 67 013 | **100.00%** |
| Budynki | 6 716 | 6 707 (+9 ekstr.) | **100.00%** |
| Firmy / Biznesy | 1 754 | 1 753 (`POIPrivate`) | **99.94%** |
| Sieć drogowa i kolejowa | 2 637 (324.8 km) | 2 637 (325.5 km) | 99.8% |
| Dzielnice / Kafelki mapy | 9 / 529 (381 odbl.) | 9 / 529 (381 odbl.) | **100.00%** |
| Pasażerowie 10 linii transportu | 10 / 10 identycznych | 10 / 10 | **100.00%** |
