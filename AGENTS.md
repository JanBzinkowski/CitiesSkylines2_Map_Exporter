# AGENTS.md — Przewodnik dla Agentów: Inżynieria Wsteczna Save z Cities: Skylines II (.cok)

Ten dokument stanowi bazę wiedzy, reguł operacyjnych i instrukcji dla agentów AI pracujących nad projektem odczytu i parsowania plików zapisu gry **Cities: Skylines II** (format `.cok` / Colossal Order Package), ze szczególnym uwzględnieniem zapisu miasta **Ożarów Mazowiecki.cok**, w korelacji z historycznymi narzędziami z **Cities: Skylines 1** oraz danymi referencyjnymi z eksportu GIS (**Carto**).

---

## 1. Cel i Misja Projektu

1. **Główny cel**: Zdekodowanie binarnego pliku zapisu `.SaveGameData` z kontenera `.cok` (Cities: Skylines II) bez konieczności uruchamiania gry, w celu bezpośredniego wyciągnięcia:
   - Geometrii dróg i sieci (węzły, segmenty, typy, szerokości, krzywe),
   - Budynków (pozycje, wymiary, strefy, poziomy, mieszkańcy, miejsca pracy),
   - Granic dzielnic i nazw,
   - Linii i tras komunikacji miejskiej,
   - Statystyk miasta (populacja, budżet, data symulacji).
2. **Punkt odniesienia (Ground Truth)**:
   - Eksport z modyfikacji **Carto** (`d4522b525766cdb8`) zawiera dokładnie wyeksportowane dane z tego samego zapisu gry. Służy do automatycznej walidacji poprawności współrzędnych, identyfikatorów i atrybutów.
3. **Kontekst historyczny**:
   - W `cs-20261121T170403Z-1-001.zip` znajduje się gotowe narzędzie odczytu save z CS1 (`.crp`), które analizowało `Assembly-CSharp.dll` i dekodowało strumień C#.

---

## 2. Co Powinieneś Umieć (Wiedza Domenowa)

### A. Różnice architektoniczne między CS1 a CS2
- **Cities: Skylines 1**:
  - Silnik: Unity klasyczny (MonoBehaviours, GameObjects).
  - Format zapisu: `.crp` (Colossal Raw Package / Unity serialized streams + nagłówek CO).
  - Odczyt: Sekwencyjny odczyt pól menedżerów (`NetManager`, `BuildingManager`, `DistrictManager`) przy użyciu deasemblera CIL (`dnfile`, `dncil`).
- **Cities: Skylines 2**:
  - Silnik: **Unity DOTS / ECS** (Data-Oriented Technology Stack: Entities, Components, Systems, Archetypes, Chunks).
  - Format kontenera: **`.cok` to standardowe archiwum ZIP (PK\x03\x04)**.
  - Składniki `.cok`:
    - `[Miasto].SaveGameMetadata` – czytelny JSON (UTF-8 z BOM) zawierający nazwę miasta, populację, pieniądze, XP, datę symulacji, opcje gry.
    - `[Miasto].SaveGameData` – binarny strumień zserializowanego świata ECS (`Colossal.IO.AssetDatabase` i `Colossal.Serialization.Entities`).
    - `[Miasto].Texture` – miniatura miasta (plik graficzny / DDS / PNG).
    - `[Mapa].Prefab` – zserializowany prefab bazowy terenu.
    - `*.cid` – identyfikatory 32-bajtowe (Content ID / GUID).
  - Format danych `.SaveGameData`:
    - Układ blokowy oparty o archetypy ECS i komponenty.
    - Na początku pliku znajdują się ciągi znaków rejestrujące mody/systemy (np. `ShortLaneOptimization`, `HomelessAndWork`).

### B. Układ współrzędnych i GIS w CS2
- Rozmiar mapy: **14.336 km × 14.336 km** (siatka 23 × 23 kafelków, każdy o wymiarach ok. 623 m × 623 m).
- Układ lokalny: metry, środek mapy to `(0, 0, wysokość)`.
- Eksport Carto mapuje to na sferoidę WGS84 w pobliżu `(0° szer., 0° dł.)` (Null Island), gdzie `0.1287° ≈ 14.3 km`.

---

## 3. Z Czego Powinieneś Korzystać (Narzędzia i Zasoby)

### Narzędzia analityczne i skrypty
1. **Python 3.15+ (Standard Library)**:
   - `zipfile` – do rozpakowywania i inspekcji kontenerów `.cok`.
   - `struct` – do dekodowania typów binarnych (int8, int16, int32, int64, float32, float64, stringi o zmiennej długości / 7-bit encoded int).
   - `json` – do operacji na metadanych i GeoJSON.
2. **Narzędzia referencyjne CS1 (w folderze `cs/`)**:
   - `cslsave.py` – model obsługi sekcji i znaczników w zapisach Colossal Order.
   - `cslinterp.py` & `cildis.py` – interpretacja instrukcji bytecode .NET do odtworzenia kolejności pól w strukturach.
   - `crp_city_export.py` – logika transformacji geometrii sieci drogowej (krzywe Béziera, węzły, łączniki).
3. **Pliki wzorcowe i testowe**:
   - Plik wejściowy: `Ożarów Mazowiecki.cok` (131 MB).
   - Pliki weryfikacyjne: `d4522b525766cdb8/GeoJSON/` (`Network_Centerline.json`, `Building_Boundary.json`, `Area_Boundary.json`, `Route_Centerline.json`).
   - Podgląd wizualny: `mapa_ozarow_cs2.html` / `city_data.js` do natychmiastowej weryfikacji wizualnej poprawności odczytanych obiektów.
4. **Dekompilacja i źródła C# gry (w razie potrzeby)**:
   - DLL silnika CS2: `Game.dll`, `Colossal.IO.AssetDatabase.dll`, `Unity.Entities.dll`.
   - Narzędzia IL/CIL: `dnfile`, `dncil` lub `ilspycmd`.

---

## 4. Zasady Działania i Dobre Praktyki

1. **Zawsze weryfikuj z danymi z Carto**:
   - Gdy odczytasz pierwszy segment drogi lub budynek z `.SaveGameData`, sprawdź, czy jego współrzędne lub nazwa pokrywają się z rekordem w `Building_Boundary.json` lub `Network_Centerline.json`.
2. **Zachowuj wydajność pamięciową**:
   - Plik `.SaveGameData` ma 130 MB. Nie wczytuj całego pliku na raz do pamięci stringów; operuj na `mmap` lub czytaj strumieniowo blokami.
3. **Batching i efektywność narzędzi**:
   - Łącz komendy weryfikujące, nie uruchamiaj pustych pętli powtarzających te same odczyty.
4. **Aktualizuj rejestr nieudanych prób**:
   - Jeśli dana hipoteza dotycząca formatu binarnego zawiodła, natychmiast zanotuj to poniżej, podając powód i zakaz powtarzania tego samego błędu.

---

## 5. Rejestr Prób i Błędów (Failed Attempts & Dead Ends Log)

*Sekcja obowiązkowa — przed przystąpieniem do jakiejkolwiek nowej próby parsowania sprawdź, czy dana ścieżka nie znajduje się na poniższej liście.*

| ID | Data | Czego próbowano | Wynik / Objaw błędu | Dlaczego to ślepy zaułek | Wniosek (Czego NIE robić) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **001** | 2026-11-21 | Traktowanie pliku `.cok` jako autorskiego, jednolitego formatu binarnego Colossal Order z CS1 (`.crp`). | Pierwsze 4 bajty to `50 4B 03 04`. | Format `.cok` to w rzeczywistości standardowe archiwum ZIP pakujące zasoby (w tym nieskompresowany `SaveGameData`). | **NIE** pisać parsera kontenera od zera — zawsze otwierać `.cok` przez standardowy `zipfile`. |
| **002** | 2026-11-21 | Próba bezpośredniego załadowania całego `Zoning_Boundary.json` (141 MB, 141 271 wielokątów) do przeglądarki w Leaflet. | Zawieszenie pamięci DOM i drastyczny spadek FPS poniżej 1 kl./s. | Plik zawiera mikroskopijne kafelki stref 8×8 m (komórki siatki), a nie same budynki. Budynki są w `Building_Boundary.json` (6 720 obiektów, 10 MB). | **NIE** wstrzykiwać surowego `Zoning_Boundary` do widoku mapy; używać zoptymalizowanego `Building_Boundary` lub kafelków rastrowych/Canvas. |
| **003** | 2026-11-21 | Wczytywanie plików GeoJSON przez `fetch()` w pliku HTML otwieranym lokalnie (`file:///...`). | Błąd przeglądarki: `CORS policy: Cross origin requests are only supported for protocol schemes: http, data...`. | Silniki Chromium (Chrome/Edge) domyślnie blokują pobieranie asynchroniczne z lokalnego dysku bez flagi lub serwera HTTP. | **NIE** polegać wyłącznie na `fetch()` przy lokalnych plikach HTML; osadzać dane w `city_data.js` (`window.CITY_DATA = ...`) jako gwarantowany, bezbłędny fallback offline. |
| **004** | 2026-11-21 | Podkładanie standardowego kafelka OpenStreetMap/Google Maps pod współrzędne z eksportu Carto. | Mapa centrowała się na Zatoce Gwinejskiej (Ocean Atlantycki przy Afryce — Null Island `0°N, 0°E`). | Eksport z gry umieszcza środek mapy w metrycznym `(0, 0)`, co po rzutowaniu na WGS84 daje punkt zerowy równika i południka Greenwich. | **NIE** wymagać podkładu mapy realnej Ziemi bez transformacji układu odniesienia; renderować tło wektorowe/Canvas lub raster z `TerrainSystem` + `WaterSystem`. |
| **005** | 2026-11-21 | Pomijanie wszystkich komponentów z flagą bitową `& 1` (tag) podczas parsowania segmentów archetypów. | Błąd wyrównania w 664 archetypach (np. `Game.Prefabs.Locked`, flaga `17`). | Flaga `17 = 1 | 16` to **enableable tag** — posiada własny segment z bitmaską aktywności. Tylko czyste tagi (`(fl & 1) and not (fl & 16)`) nie mają segmentu danych. | **NIE** pomijać komponentów z flagą `& 16` przy liczeniu segmentów w bloku archetypu. |
| **006** | 2026-11-21 | Dekodowanie `Game.Net.Curve` (48 B) z rozmiarem elementu `esz=48` zamiast `esz=12`. | Zwracało śmieciowe floaty (`-2.1e6`, `1e32`). | `Net.Curve` to 4 punkty kontrolne `float3` (Bézier 3. stopnia) przetasowane bajtowo z krokiem `esz=12` (rozmiar `float3`), nie `48`. | **NIE** zakładać `esz = rozmiar_struktury` dla flagi `32`; dla `Net.Curve` zawsze używać `decode32(seg, 12)`. |
| **007** | 2026-11-21 | Odczyt stringów w `Game.UI.NameSystem` traktując prefiks `u32` jako liczbę bajtów UTF-8. | `UnicodeDecodeError` przy polskich znakach (`Os. Zientarówka` ucięte o 1 bajt). | Colossal Order zapisuje `C# string.Length` (liczbę znaków/kodów Unicode), a następnie ich bajty w UTF-8. | **NIE** czytać `sl` bajtów z `NameSystem`; zawsze czytać `sl` znaków UTF-8 (`read_utf8_chars`). |
| **008** | 2026-11-21 | Branie pierwszej siatki `4096×4096` w `TerrainSystem` jako obszaru grywalnego oraz traktowanie `WaterSystem` jako wartości nieujemnych. | Błąd wysokości terenu ~155 m względem budynków; ujemne głębokości w `WaterSystem`. | Pierwsza siatka w `TerrainSystem` (offset 12) to **worldmap (57.3 km)**, dopiero druga (offset `12 + 4096*4096*2 + 8`) to obszar grywalny **14.336 km** (`iz*4096+ix`). W `WaterSystem` suchy ląd ma wartości **ujemne**, a prawdziwa woda `depth > 0.08`. | **NIE** używać mapy 1 z `TerrainSystem` jako wysokości miasta ani nie traktować wartości ujemnych z `WaterSystem` jako błędu. |

---

## 6. Rozszyfrowana Specyfikacja `.cok` (Zweryfikowana 100% vs Carto)

1. **Kontener i bloki Zstd (`cs2save.py`)**:
   - `.cok` = ZIP (STORED) → `<Miasto>.SaveGameData` (18 B nagłówka + `u16 nTags` + `nTags` stringów + sekwencja bloków `[u32 rawSize][u32 zSize][zstd frame]`, puste bloki mają `zSize=0`).
   - Blok `0`: tabela typów komponentów ECS (flaga `1`=tag, `2`=component, `4`=buffer, `8`=shared, `16`=enableable, `32`=delta+byte-shuffle).
   - Blok `1`: tabela systemów (ostatnie `nSys` bloków to zserializowane systemy).
   - Blok `2`: tabela archetypów (wszystkie 1736 archetypów parsuje się co do bajtu).
   - Referencje encji: `i32` indeks globalny po konkatenacji archetypów (`-1` = null).
2. **Dekodowanie danych (`cok_to_html.py`)**:
   - `decode32(b, esz)`: kumulatywna suma bajtów mod 256 + unshuffle z krokiem `esz` (`PrefabRef`: 4, `Net.Curve`: 12, `Areas.Node`: 16, `Net.Node`: 28).
   - `Buildings`: `Objects.Transform` (pos + kwaternion, obrót Y ze znakiem `+2*qw*qy`) + `ObjectGeometryData.m_Bounds` / `BuildingData.m_LotSize`; populacja przez `HouseholdMember → Household → PropertyRenter`, etaty przez `Companies.Employee`.
   - `Transit`: dokładne trasy po pasach z `Routes.Segment → Pathfind.PathElement` (kodowanie półbajtowe `lo/hi: 0→0.0, 1→1.0, 2→f32`) → `Net.Curve`; pasażerowie przez `Creatures.CurrentVehicle → Vehicles.LayoutElement → Routes.CurrentRoute` (**10/10 linii 100% zgodnych z Carto**).
   - `Terrain & Water`: `TerrainSystem` (map2, `4096×4096 u16`, `scale=4096/65535`) + `WaterSystem` (offset 28, `2048×2048 f32` płaszczyzny 0–3, `depth > 0.08` = woda).
   - Narzędzie końcowe: `python cok_to_html.py "Ożarów Mazowiecki.cok" -o ozarow_z_cok.html` → samodzielny, pojedynczy plik HTML (3.5 MB, działa offline bez CORS).
