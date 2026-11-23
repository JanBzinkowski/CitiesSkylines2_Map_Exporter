"""exporter.py — Batch exporter for Cities: Skylines II (.cok) saves -> standalone HTML maps + Koru GeoJSON.

Folder layout (next to this script):
    to_export/          <- place .cok save files here
    already_exported/   <- processed .cok files are moved here after successful export
    exported_htmls/     <- generated standalone Google Maps-style .html maps and CSVs
    exported_geojson/   <- generated GeoJSON datasets conforming 100% to Koru schemas
    export_error/       <- failed .cok files are moved here + logged to 'export error.txt'

Usage:
    python exporter.py                # export all (.html, .geojson, .csv)
    python exporter.py --no-html      # skip HTML (or --nohtml)
    python exporter.py --no-csv       # skip CSV (or --nocsv)
    python exporter.py --no-geojson   # skip GeoJSON (or --nogeojson)
    python exporter.py --regen-all    # regenerate exports for saves already in already_exported/
    python exporter.py --crs local    # export GeoJSON with local meter coordinates instead of WGS84
"""
import argparse
import datetime
import os
import shutil
import sys
import time
import traceback
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from cok_to_html import (
    extract_city_from_cok,
    write_businesses_csv,
    write_koru_geojson,
    write_standalone_html,
)

TO_EXPORT_DIR = os.path.join(BASE_DIR, "to_export")
ALREADY_EXPORTED_DIR = os.path.join(BASE_DIR, "already_exported")
EXPORTED_HTMLS_DIR = os.path.join(BASE_DIR, "exported_htmls")
EXPORTED_GEOJSON_DIR = os.path.join(BASE_DIR, "exported_geojson")
EXPORT_ERROR_DIR = os.path.join(BASE_DIR, "export_error")
EXPORT_ERROR_LOG = os.path.join(EXPORT_ERROR_DIR, "export error.txt")


def ensure_dirs():
    for d in (TO_EXPORT_DIR, ALREADY_EXPORTED_DIR, EXPORTED_HTMLS_DIR, EXPORTED_GEOJSON_DIR, EXPORT_ERROR_DIR):
        os.makedirs(d, exist_ok=True)


def list_coks(folder: str):
    if not os.path.isdir(folder):
        return []
    return sorted(
        f for f in os.listdir(folder)
        if f.lower().endswith(".cok") and os.path.isfile(os.path.join(folder, f))
    )


def export_one(
    cok_path: str,
    stem: str,
    export_html: bool = True,
    export_geojson: bool = True,
    export_csv: bool = True,
    include_terrain: bool = True,
    crs_mode: str = "geographic",
):
    # Only render terrain raster if HTML output is requested
    city_data = extract_city_from_cok(
        cok_path,
        include_terrain=(include_terrain and export_html),
    )

    if export_html:
        out_html_path = os.path.join(EXPORTED_HTMLS_DIR, f"{stem}.html")
        write_standalone_html(city_data, out_html_path)

    if export_geojson:
        out_geojson_path = os.path.join(EXPORTED_GEOJSON_DIR, f"{stem}.geojson")
        write_koru_geojson(city_data, out_geojson_path, crs_mode=crs_mode)

    if export_csv:
        out_csv_path = os.path.join(EXPORTED_HTMLS_DIR, f"{stem}_biznesy.csv")
        write_businesses_csv(city_data, out_csv_path)


def move_file(src_path: str, dst_dir: str) -> str:
    fname = os.path.basename(src_path)
    dst_path = os.path.join(dst_dir, fname)
    if os.path.abspath(src_path) == os.path.abspath(dst_path):
        return dst_path
    if os.path.exists(dst_path):
        os.remove(dst_path)
    shutil.move(src_path, dst_path)
    return dst_path


def describe_error(exc: Exception) -> str:
    if isinstance(exc, zipfile.BadZipFile):
        return "Plik .cok jest uszkodzony lub nie jest prawidłowym archiwum ZIP."
    if isinstance(exc, IndexError):
        return "Brak oczekiwanego wpisu (np. .SaveGameData) wewnątrz archiwum .cok lub niepełny strumień."
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return "Brak wolnego miejsca na dysku podczas zapisu pliku."
    if isinstance(exc, AssertionError):
        return "Niezgodność struktury binarnej ECS (możliwa inna wersja gry lub nieobsługiwany format segmentu)."
    return f"Nieoczekiwany błąd podczas parsowania lub zapisu ({type(exc).__name__})."


def log_export_error(fname: str, file_size: int, exc: Exception, tb_text: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = (
        f"========================================================================\n"
        f"Data      : {ts}\n"
        f"Plik      : {fname} ({file_size:,} bajtów)\n"
        f"Przyczyna : {describe_error(exc)}\n"
        f"Wyjątek   : {type(exc).__name__}: {exc}\n"
        f"--- Szczegóły (traceback) ---\n"
        f"{tb_text.rstrip()}\n"
        f"========================================================================\n\n"
    )
    with open(EXPORT_ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(entry)


def main():
    ap = argparse.ArgumentParser(description="Batch export Cities: Skylines II .cok saves to HTML maps and Koru GeoJSON")
    ap.add_argument("--regen-all", action="store_true", help="Also regenerate exports for all .cok files in already_exported/")
    ap.add_argument("--no-terrain", action="store_true", help="Skip terrain/water PNG generation (faster)")
    ap.add_argument("--no-html", "--nohtml", dest="no_html", action="store_true", help="Skip generating standalone HTML maps")
    ap.add_argument("--no-csv", "--nocsv", dest="no_csv", action="store_true", help="Skip generating businesses CSV files")
    ap.add_argument("--no-geojson", "--nogeojson", dest="no_geojson", action="store_true", help="Skip generating Koru GeoJSON files")
    ap.add_argument("--crs", choices=["geographic", "local"], default="geographic", help="CRS for GeoJSON: geographic (WGS84 EPSG:4326) or local (meters)")
    args = ap.parse_args()

    ensure_dirs()
    include_terrain = not args.no_terrain
    export_html = not args.no_html
    export_geojson = not args.no_geojson
    export_csv = not args.no_csv

    queue = []
    for f in list_coks(TO_EXPORT_DIR):
        queue.append((os.path.join(TO_EXPORT_DIR, f), True))
    if args.regen_all:
        for f in list_coks(ALREADY_EXPORTED_DIR):
            queue.append((os.path.join(ALREADY_EXPORTED_DIR, f), False))

    if not queue:
        print(f"Brak plikow .cok w folderze: {TO_EXPORT_DIR}")
        print("Wrzuc pliki .cok do 'to_export' i uruchom ponownie (lub uzyj --regen-all).")
        return

    active_outputs = []
    if export_html:
        active_outputs.append("HTML")
    if export_geojson:
        active_outputs.append(f"GeoJSON (CRS: {args.crs})")
    if export_csv:
        active_outputs.append("CSV")
    print(f"Znaleziono {len(queue)} plik(ow) .cok do eksportu. Aktywne formaty: {', '.join(active_outputs) or 'brak'}\n")

    t_all = time.time()
    ok = 0
    failed = 0

    for idx, (cok_path, should_move) in enumerate(queue, 1):
        fname = os.path.basename(cok_path)
        stem = os.path.splitext(fname)[0]
        file_size = os.path.getsize(cok_path) if os.path.exists(cok_path) else 0
        print(f"[{idx}/{len(queue)}] Eksport: {fname}")
        try:
            export_one(
                cok_path,
                stem,
                export_html=export_html,
                export_geojson=export_geojson,
                export_csv=export_csv,
                include_terrain=include_terrain,
                crs_mode=args.crs,
            )
            if should_move:
                dst = move_file(cok_path, ALREADY_EXPORTED_DIR)
                print(f"  Przeniesiono save -> already_exported/{os.path.basename(dst)}")
            ok += 1
        except Exception as exc:
            failed += 1
            tb_text = traceback.format_exc()
            print(f"  BLAD przy eksporcie {fname}: {type(exc).__name__}: {exc}")
            dst = move_file(cok_path, EXPORT_ERROR_DIR)
            log_export_error(fname, file_size, exc, tb_text)
            print(f"  Przeniesiono uszkodzony/problematyczny save -> export_error/{os.path.basename(dst)}")
            print(f"  Zapisano raport bledu -> export_error/export error.txt")
        print()

    print(f"=== Zakonczono w {time.time() - t_all:.1f}s: sukces={ok}, bledy={failed} ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

