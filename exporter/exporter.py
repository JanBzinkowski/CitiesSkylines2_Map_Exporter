"""exporter.py — Batch exporter for Cities: Skylines II (.cok) saves -> standalone HTML maps.

Folder layout (next to this script):
    to_export/          <- place .cok save files here
    already_exported/   <- processed .cok files are moved here after successful export
    exported_htmls/     <- generated standalone Google Maps-style .html files
    export_error/       <- failed .cok files are moved here + logged to 'export error.txt'

Usage:
    python exporter.py                # export all .cok files from to_export/ and move them to already_exported/
    python exporter.py --regen-all    # also regenerate HTMLs for saves already in already_exported/
    python exporter.py --no-terrain   # skip terrain/water raster overlay for faster export
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

from cok_to_html import extract_city_from_cok, write_businesses_csv, write_standalone_html

TO_EXPORT_DIR = os.path.join(BASE_DIR, "to_export")
ALREADY_EXPORTED_DIR = os.path.join(BASE_DIR, "already_exported")
EXPORTED_HTMLS_DIR = os.path.join(BASE_DIR, "exported_htmls")
EXPORT_ERROR_DIR = os.path.join(BASE_DIR, "export_error")
EXPORT_ERROR_LOG = os.path.join(EXPORT_ERROR_DIR, "export error.txt")


def ensure_dirs():
    for d in (TO_EXPORT_DIR, ALREADY_EXPORTED_DIR, EXPORTED_HTMLS_DIR, EXPORT_ERROR_DIR):
        os.makedirs(d, exist_ok=True)


def list_coks(folder: str):
    if not os.path.isdir(folder):
        return []
    return sorted(
        f for f in os.listdir(folder)
        if f.lower().endswith(".cok") and os.path.isfile(os.path.join(folder, f))
    )


def export_one(cok_path: str, out_html_path: str, include_terrain: bool = True):
    city_data = extract_city_from_cok(cok_path, include_terrain=include_terrain)
    write_standalone_html(city_data, out_html_path)
    out_csv_path = os.path.splitext(out_html_path)[0] + "_biznesy.csv"
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
        return "Brak wolnego miejsca na dysku podczas zapisu pliku HTML."
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
    ap = argparse.ArgumentParser(description="Batch export Cities: Skylines II .cok saves to HTML maps")
    ap.add_argument("--regen-all", action="store_true", help="Also regenerate HTMLs for all .cok files in already_exported/")
    ap.add_argument("--no-terrain", action="store_true", help="Skip terrain/water PNG generation (faster)")
    args = ap.parse_args()

    ensure_dirs()
    include_terrain = not args.no_terrain

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

    print(f"Znaleziono {len(queue)} plik(ow) .cok do eksportu.\n")
    t_all = time.time()
    ok = 0
    failed = 0

    for idx, (cok_path, should_move) in enumerate(queue, 1):
        fname = os.path.basename(cok_path)
        stem = os.path.splitext(fname)[0]
        out_html = os.path.join(EXPORTED_HTMLS_DIR, f"{stem}.html")
        file_size = os.path.getsize(cok_path) if os.path.exists(cok_path) else 0
        print(f"[{idx}/{len(queue)}] Eksport: {fname} -> exported_htmls/{stem}.html")
        try:
            export_one(cok_path, out_html, include_terrain=include_terrain)
            if should_move:
                dst = move_file(cok_path, ALREADY_EXPORTED_DIR)
                print(f"  Przeniesiono save -> already_exported/{os.path.basename(dst)}")
            ok += 1
        except Exception as exc:
            failed += 1
            tb_text = traceback.format_exc()
            print(f"  BLAD przy eksporcie {fname}: {type(exc).__name__}: {exc}")
            # Remove partial HTML if created
            if os.path.exists(out_html):
                try:
                    os.remove(out_html)
                except OSError:
                    pass
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
