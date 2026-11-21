"""reddit_scraper.py — Official Reddit OAuth2 collector for Cities: Skylines II (.cok) saves.

Searches r/CitiesSkylines2 (and any extra --subs) for posts/comments sharing .cok or .zip
savegames, validates that downloaded files are real CS2 saves (ZIP containing *.SaveGameData),
and places them into exporter/to_export/.

Setup (30 seconds, free official Reddit API):
  1. Go to https://www.reddit.com/prefs/apps -> "create another app..." -> choose "script".
  2. Set redirect uri to http://localhost:8080 and create the app.
  3. Copy reddit_config.example.json -> reddit_config.json and paste client_id & client_secret
     (or set env vars REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET).

Usage:
    python reddit_scraper.py --hours 4
    python reddit_scraper.py --hours 6 --auto-export --min-free-mb 1000
    python reddit_scraper.py --subs CitiesSkylines2 CitiesSkylines --hours 3
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

TO_EXPORT_DIR = os.path.join(BASE_DIR, "to_export")
ALREADY_EXPORTED_DIR = os.path.join(BASE_DIR, "already_exported")
EXPORT_ERROR_DIR = os.path.join(BASE_DIR, "export_error")
CONFIG_PATH = os.path.join(BASE_DIR, "reddit_config.json")
STATE_PATH = os.path.join(BASE_DIR, "scraper_state.json")
MANUAL_LINKS_PATH = os.path.join(BASE_DIR, "manual_links.txt")

SEARCH_QUERIES = [
    ".cok",
    "savegame",
    '"save file"',
    '"save game"',
    "drive.google.com",
    "dropbox.com",
    '"fix my city"',
    '"fix my traffic"',
    '"share my city"',
]

SAVE_HINT_RE = re.compile(
    r"(\.cok\b|save\s*game|save\s*file|savegame|fix\s+my\s+(city|traffic)|"
    r"drive\.google\.com|dropbox\.com|mediafire\.com|mega\.nz|gofile\.io|1drv\.ms|onedrive|mods\.paradoxplaza\.com)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`]+", re.IGNORECASE)
GDRIVE_ID_RE = re.compile(
    r"(?:drive\.google\.com/(?:file/d/|open\?id=|uc\?.*id=)|drive\.usercontent\.google\.com/download\?.*id=)([A-Za-z0-9_-]{15,})"
)

MANUAL_HOSTS = (
    "mega.nz",
    "mediafire.com",
    "gofile.io",
    "1drv.ms",
    "onedrive.live.com",
    "mods.paradoxplaza.com",
    "wetransfer.com",
    "pixeldrain.com",
)


def log(msg: str):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def sanitize_filename(name: str, fallback: str = "city_save") -> str:
    name = urllib.parse.unquote(name or "").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = fallback
    if len(name) > 90:
        name = name[:90].strip(" .")
    return name


def load_config():
    cid = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    csec = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    ua = os.environ.get("REDDIT_USER_AGENT", "").strip()
    if os.path.exists(CONFIG_PATH):
        cfg = json.load(open(CONFIG_PATH, "r", encoding="utf-8"))
        cid = cid or cfg.get("client_id", "").strip()
        csec = csec or cfg.get("client_secret", "").strip()
        ua = ua or cfg.get("user_agent", "").strip()
    if not ua:
        ua = "python:CS2SaveCollector:v1.0 (personal CS2 save archiver)"
    return cid, csec, ua


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            st = json.load(open(STATE_PATH, "r", encoding="utf-8"))
            return {
                "seen_posts": set(st.get("seen_posts", [])),
                "seen_urls": set(st.get("seen_urls", [])),
                "seen_hashes": set(st.get("seen_hashes", [])),
                "manual_urls": set(st.get("manual_urls", [])),
            }
        except Exception:
            pass
    return {"seen_posts": set(), "seen_urls": set(), "seen_hashes": set(), "manual_urls": set()}


def save_state(state: dict):
    out = {k: sorted(v) for k, v in state.items()}
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)


def free_disk_mb(path: str) -> float:
    return shutil.disk_usage(path).free / (1024 * 1024)


def clean_url(u: str) -> str:
    return u.rstrip(".,;:!?)\"'\\")


def host_matches(host: str, domains) -> bool:
    host = host.split(":")[0].lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def classify_download_url(url: str):
    """Return (kind, direct_url) where kind is 'direct', 'manual', or 'ignore'."""
    u = clean_url(url)
    parsed = urllib.parse.urlparse(u)
    host = parsed.netloc.lower()
    path_l = parsed.path.lower()

    # Ignore Reddit/image/video/social links
    if host_matches(
        host,
        (
            "reddit.com",
            "redd.it",
            "imgur.com",
            "youtube.com",
            "youtu.be",
            "twitch.tv",
            "twitter.com",
            "x.com",
            "discord.gg",
            "steamcommunity.com",
            "steampowered.com",
            "wikipedia.org",
        ),
    ):
        return "ignore", u

    # Google Drive direct public download URL
    m = GDRIVE_ID_RE.search(u)
    if m:
        fid = m.group(1)
        return "direct", f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"

    # Dropbox public file link -> dl=1
    if host_matches(host, ("dropbox.com",)) and ("/s/" in path_l or "/scl/fi/" in path_l):
        qs = urllib.parse.parse_qs(parsed.query)
        qs["dl"] = ["1"]
        new_q = urllib.parse.urlencode(qs, doseq=True)
        direct = urllib.parse.urlunparse(parsed._replace(query=new_q))
        return "direct", direct

    # Direct .cok or .zip URL
    if path_l.endswith(".cok") or path_l.endswith(".zip"):
        return "direct", u

    # Known hosts that typically require interactive browser download
    if host_matches(host, MANUAL_HOSTS):
        return "manual", u

    return "ignore", u


def is_valid_cok_zip(path: str) -> bool:
    """Return True if `path` is a valid ZIP containing a CS2 *.SaveGameData entry."""
    try:
        with zipfile.ZipFile(path, "r") as z:
            return any(n.endswith(".SaveGameData") for n in z.namelist())
    except Exception:
        return False


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def unique_dest_path(folder: str, base_name: str) -> str:
    stem = sanitize_filename(base_name, "city_save")
    if stem.lower().endswith(".cok"):
        stem = stem[:-4]
    cand = os.path.join(folder, f"{stem}.cok")
    idx = 2
    while (
        os.path.exists(cand)
        or os.path.exists(os.path.join(ALREADY_EXPORTED_DIR, os.path.basename(cand)))
        or os.path.exists(os.path.join(EXPORT_ERROR_DIR, os.path.basename(cand)))
    ):
        cand = os.path.join(folder, f"{stem}_{idx}.cok")
        idx += 1
    return cand


def extract_filename_from_headers(headers, fallback_url: str) -> str:
    cd = headers.get("Content-Disposition", "") if headers else ""
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.IGNORECASE)
    if m:
        return urllib.parse.unquote(m.group(1)).strip(' "')
    m = re.search(r'filename="?([^";]+)"?', cd, re.IGNORECASE)
    if m:
        return m.group(1).strip(' "')
    base = os.path.basename(urllib.parse.urlparse(fallback_url).path)
    return base or "save"


def download_and_validate(direct_url: str, post_id: str, post_title: str, user_agent: str, state: dict, max_size_mb: int, min_free_mb: int):
    """Download a candidate URL to temp file, verify PK header & .SaveGameData, place into to_export/.
    Returns list of saved .cok file paths.
    """
    if free_disk_mb(TO_EXPORT_DIR) < min_free_mb:
        log(f"  STOP/POMINIETO: za malo wolnego miejsca na dysku ({free_disk_mb(TO_EXPORT_DIR):.0f} MB < {min_free_mb} MB).")
        return []

    req = urllib.request.Request(direct_url, headers={"User-Agent": user_agent})
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="cs2dl_", suffix=".part", dir=TO_EXPORT_DIR)
    os.close(tmp_fd)
    saved = []

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit():
                mb = int(cl) / (1024 * 1024)
                if mb > max_size_mb:
                    log(f"  Pominieto (za duzy: {mb:.1f} MB > {max_size_mb} MB): {direct_url[:80]}")
                    return []
                if free_disk_mb(TO_EXPORT_DIR) - mb < min_free_mb:
                    log(f"  Pominieto (plik {mb:.1f} MB przekroczylby limit wolnego miejsca).")
                    return []

            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype:
                log(f"  Pominieto (strona HTML / wymaga zgody lub logowania): {direct_url[:80]}")
                return []

            first_chunk = resp.read(65536)
            if not first_chunk or not first_chunk.startswith(b"PK\x03\x04"):
                log(f"  Pominieto (nie jest archiwum ZIP/.cok, magic={first_chunk[:8]!r}): {direct_url[:80]}")
                return []

            total = len(first_chunk)
            max_bytes = max_size_mb * 1024 * 1024
            with open(tmp_path, "wb") as out:
                out.write(first_chunk)
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        log(f"  Przerwano (przekroczono limit {max_size_mb} MB).")
                        return []
                    out.write(chunk)

            hdr_name = extract_filename_from_headers(resp.headers, direct_url)
            log(f"  Pobrano {total / (1024*1024):.1f} MB ({hdr_name}) — weryfikacja .cok...")

        # Case A: direct .cok file
        if is_valid_cok_zip(tmp_path):
            sha = file_sha256(tmp_path)
            if sha in state["seen_hashes"]:
                log("  Duplikat (ten sam SHA-256 juz byl pobrany) — pominieto.")
                return []
            base = hdr_name if hdr_name.lower().endswith(".cok") else f"{post_id}_{post_title[:45]}"
            dst = unique_dest_path(TO_EXPORT_DIR, base)
            shutil.move(tmp_path, dst)
            state["seen_hashes"].add(sha)
            saved.append(dst)
            log(f"  OK! Zapisano save CS2 -> to_export/{os.path.basename(dst)}")
            return saved

        # Case B: .zip wrapper containing one or more .cok files
        try:
            with zipfile.ZipFile(tmp_path, "r") as outer:
                cok_entries = [n for n in outer.namelist() if n.lower().endswith(".cok") and not n.endswith("/")]
                for entry in cok_entries:
                    inner_fd, inner_tmp = tempfile.mkstemp(prefix="cs2in_", suffix=".cok", dir=TO_EXPORT_DIR)
                    os.close(inner_fd)
                    try:
                        with outer.open(entry) as src, open(inner_tmp, "wb") as dst_f:
                            shutil.copyfileobj(src, dst_f, length=262144)
                        if not is_valid_cok_zip(inner_tmp):
                            os.remove(inner_tmp)
                            continue
                        sha = file_sha256(inner_tmp)
                        if sha in state["seen_hashes"]:
                            os.remove(inner_tmp)
                            log(f"  Duplikat wewnatrz ZIP ({entry}) — pominieto.")
                            continue
                        dst = unique_dest_path(TO_EXPORT_DIR, os.path.basename(entry))
                        shutil.move(inner_tmp, dst)
                        state["seen_hashes"].add(sha)
                        saved.append(dst)
                        log(f"  OK! Wypakowano save z ZIP -> to_export/{os.path.basename(dst)}")
                    finally:
                        if os.path.exists(inner_tmp):
                            try:
                                os.remove(inner_tmp)
                            except OSError:
                                pass
        except zipfile.BadZipFile:
            pass

        if not saved:
            log("  Pominieto (ZIP nie zawiera zadnego pliku .cok / .SaveGameData).")
        return saved
    except Exception as exc:
        log(f"  Blad pobierania {direct_url[:80]}: {type(exc).__name__}: {exc}")
        return []
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


class RedditOAuthClient:
    """Minimal pure-Python client for Reddit's official OAuth2 API (oauth.reddit.com)."""

    def __init__(self, client_id: str, client_secret: str, user_agent: str, delay_sec: float = 2.0):
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_agent = user_agent
        self.delay_sec = delay_sec
        self.token = None
        self.token_exp = 0.0
        self.last_req = 0.0

    def _ensure_token(self):
        if self.token and time.time() < self.token_exp - 60:
            return
        auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("ascii")
        data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8")
        req = urllib.request.Request(
            "https://www.reddit.com/api/v1/access_token",
            data=data,
            headers={
                "Authorization": f"Basic {auth}",
                "User-Agent": self.user_agent,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8"))
        self.token = res["access_token"]
        self.token_exp = time.time() + int(res.get("expires_in", 3600))
        log("Uzyskano token Reddit OAuth2 (wazny ~1h).")

    def get_json(self, path: str, params: dict = None):
        self._ensure_token()
        wait = self.delay_sec - (time.time() - self.last_req)
        if wait > 0:
            time.sleep(wait)

        url = "https://oauth.reddit.com" + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        for attempt in range(4):
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self.token}", "User-Agent": self.user_agent},
            )
            self.last_req = time.time()
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    rem = r.headers.get("X-Ratelimit-Remaining")
                    rst = r.headers.get("X-Ratelimit-Reset")
                    body = json.loads(r.read().decode("utf-8"))
                    if rem is not None and float(rem) < 3 and rst:
                        sleep_s = min(65.0, float(rst) + 1.0)
                        log(f"  Limit API Reddit bliski 0 — pauza {sleep_s:.0f}s...")
                        time.sleep(sleep_s)
                    return body
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    self.token = None
                    self._ensure_token()
                    continue
                if e.code == 429:
                    retry = float(e.headers.get("Retry-After", 30))
                    log(f"  HTTP 429 (rate limit) — czekam {retry:.0f}s...")
                    time.sleep(min(120.0, retry + 2.0))
                    continue
                if e.code in (500, 502, 503, 504):
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        return None


def collect_comment_urls(node, out_urls: list):
    if isinstance(node, list):
        for item in node:
            collect_comment_urls(item, out_urls)
    elif isinstance(node, dict):
        data = node.get("data", {})
        if isinstance(data, dict):
            body = data.get("body", "")
            if body:
                out_urls.extend(URL_RE.findall(body))
            if "children" in data:
                collect_comment_urls(data["children"], out_urls)
            if isinstance(data.get("replies"), dict):
                collect_comment_urls(data["replies"], out_urls)


def record_manual_link(url: str, post_title: str, permalink: str, state: dict):
    if url in state["manual_urls"]:
        return
    state["manual_urls"].add(url)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(MANUAL_LINKS_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {post_title}\n  Reddit: https://www.reddit.com{permalink}\n  Link  : {url}\n\n")


def process_post(client: RedditOAuthClient, post: dict, state: dict, args, auto_export_fn=None) -> int:
    pid = post.get("id", "")
    if not pid or pid in state["seen_posts"]:
        return 0

    title = post.get("title", "")
    selftext = post.get("selftext", "")
    post_url = post.get("url", "")
    permalink = post.get("permalink", "")
    combined = f"{title}\n{selftext}\n{post_url}"

    # Only fetch comments if post looks save-related (saves API calls!)
    looks_like_save = bool(SAVE_HINT_RE.search(combined))
    state["seen_posts"].add(pid)
    if not looks_like_save:
        return 0

    urls = []
    if post_url:
        urls.append(post_url)
    urls.extend(URL_RE.findall(selftext))

    # Fetch comments to catch links posted by OP in replies
    if post.get("num_comments", 0) > 0:
        try:
            c_json = client.get_json(f"/r/{post.get('subreddit')}/comments/{pid}", {"limit": 50, "depth": 3})
            if c_json:
                collect_comment_urls(c_json, urls)
        except Exception as exc:
            log(f"  Nie udalo sie pobrac komentarzy dla {pid}: {exc}")

    downloaded = 0
    for raw_u in dict.fromkeys(clean_url(u) for u in urls):
        if raw_u in state["seen_urls"]:
            continue
        kind, direct_u = classify_download_url(raw_u)
        if kind == "ignore":
            continue
        state["seen_urls"].add(raw_u)
        if kind == "manual":
            log(f"  [Reczny link] {title[:55]} -> {direct_u[:70]}")
            record_manual_link(direct_u, title, permalink, state)
            continue
        if kind == "direct":
            log(f"Znaleziono kandydata w '{title[:60]}' -> {direct_u[:75]}")
            saved = download_and_validate(
                direct_u, pid, title, client.user_agent, state, args.max_size_mb, args.min_free_mb
            )
            downloaded += len(saved)
            if saved and auto_export_fn:
                for cok_p in saved:
                    auto_export_fn(cok_p)
    save_state(state)
    return downloaded


def run_auto_export_single(cok_path: str, no_terrain: bool = False):
    import traceback
    import exporter as exp

    exp.ensure_dirs()
    fname = os.path.basename(cok_path)
    stem = os.path.splitext(fname)[0]
    out_html = os.path.join(exp.EXPORTED_HTMLS_DIR, f"{stem}.html")
    fsize = os.path.getsize(cok_path) if os.path.exists(cok_path) else 0
    try:
        log(f"  [Auto-Export] {fname} -> exported_htmls/{stem}.html")
        exp.export_one(cok_path, out_html, include_terrain=not no_terrain)
        exp.move_file(cok_path, exp.ALREADY_EXPORTED_DIR)
        log(f"  [Auto-Export] OK -> already_exported/{fname}")
    except Exception as exc:
        if os.path.exists(out_html):
            try:
                os.remove(out_html)
            except OSError:
                pass
        exp.move_file(cok_path, exp.EXPORT_ERROR_DIR)
        exp.log_export_error(fname, fsize, exc, traceback.format_exc())
        log(f"  [Auto-Export] BLAD -> przeniesiono do export_error/{fname}")


def main():
    ap = argparse.ArgumentParser(description="Collect CS2 (.cok) saves from Reddit into exporter/to_export/")
    ap.add_argument("--subs", nargs="+", default=["CitiesSkylines2"], help="Subreddits to scan (default: CitiesSkylines2)")
    ap.add_argument("--hours", type=float, default=4.0, help="How many hours to run (default: 4.0)")
    ap.add_argument("--poll-minutes", type=float, default=15.0, help="Minutes between /new polls after backlog scan")
    ap.add_argument("--max-pages", type=int, default=8, help="Max pagination pages per query/listing (default: 8)")
    ap.add_argument("--max-size-mb", type=int, default=500, help="Skip single downloads larger than this MB (default: 500)")
    ap.add_argument("--min-free-mb", type=int, default=500, help="Stop downloading if free disk drops below this MB (default: 500)")
    ap.add_argument("--delay", type=float, default=2.2, help="Seconds between Reddit API calls (default: 2.2)")
    ap.add_argument("--auto-export", action="store_true", help="Automatically export each downloaded .cok to HTML")
    ap.add_argument("--no-terrain", action="store_true", help="With --auto-export, skip terrain/water raster")
    args = ap.parse_args()

    for d in (TO_EXPORT_DIR, ALREADY_EXPORTED_DIR, EXPORT_ERROR_DIR):
        os.makedirs(d, exist_ok=True)

    cid, csec, ua = load_config()
    if not cid or not csec or "TUTAJ" in cid:
        print("Brak danych logowania Reddit OAuth2 (client_id / client_secret)!")
        print(f"1. Wejdz na https://www.reddit.com/prefs/apps -> 'create another app...' -> wybierz 'script'.")
        print(f"2. Ustaw redirect uri np. http://localhost:8080.")
        print(f"3. Skopiuj 'reddit_config.example.json' jako '{CONFIG_PATH}' i wpisz client_id oraz client_secret.")
        sys.exit(2)

    if free_disk_mb(TO_EXPORT_DIR) < args.min_free_mb:
        log(f"UWAGA: Wolne miejsce na dysku ({free_disk_mb(TO_EXPORT_DIR):.0f} MB) < --min-free-mb ({args.min_free_mb} MB).")
        log("Zwolnij miejsce na dysku przed uruchomieniem lub zmien --min-free-mb.")
        sys.exit(3)

    client = RedditOAuthClient(cid, csec, ua, delay_sec=args.delay)
    state = load_state()
    deadline = time.time() + args.hours * 3600.0
    auto_fn = (lambda p: run_auto_export_single(p, args.no_terrain)) if args.auto_export else None

    total_saved = 0
    log(f"Start Reddit CS2 Save Collector | subs={args.subs} | czas={args.hours}h | wolne miejsce={free_disk_mb(TO_EXPORT_DIR):.0f} MB")

    def walk_listing(path: str, base_params: dict, label: str):
        nonlocal total_saved
        after = None
        for page in range(args.max_pages):
            if time.time() >= deadline:
                return
            if free_disk_mb(TO_EXPORT_DIR) < args.min_free_mb:
                log("Osiagnieto limit wolnego miejsca na dysku — koncze pobieranie.")
                return
            params = dict(base_params, limit=100)
            if after:
                params["after"] = after
            log(f"Skanowanie {label} (strona {page + 1})...")
            data = client.get_json(path, params)
            if not data or "data" not in data:
                break
            children = data["data"].get("children", [])
            if not children:
                break
            for ch in children:
                if time.time() >= deadline:
                    return
                total_saved += process_post(client, ch.get("data", {}), state, args, auto_fn)
            after = data["data"].get("after")
            if not after:
                break

    try:
        # Phase 1: Historical targeted searches + top/new listings
        for sub in args.subs:
            for q in SEARCH_QUERIES:
                if time.time() >= deadline:
                    break
                walk_listing(
                    f"/r/{sub}/search",
                    {"q": q, "restrict_sr": 1, "sort": "new", "t": "all"},
                    f"r/{sub} search={q}",
                )
            for sort in ("new", "top"):
                if time.time() >= deadline:
                    break
                p = {"t": "year"} if sort == "top" else {}
                walk_listing(f"/r/{sub}/{sort}", p, f"r/{sub}/{sort}")

        # Phase 2: Keep polling /new until --hours elapsed
        while time.time() < deadline:
            if free_disk_mb(TO_EXPORT_DIR) < args.min_free_mb:
                log("Zatrzymano: malo wolnego miejsca na dysku.")
                break
            rem_min = (deadline - time.time()) / 60.0
            sleep_s = min(args.poll_minutes * 60.0, max(1.0, deadline - time.time()))
            log(f"Pauza {sleep_s/60:.1f} min przed kolejnym sprawdzeniem /new (pozostalo {rem_min:.0f} min, pobrano dotad: {total_saved})...")
            time.sleep(sleep_s)
            if time.time() >= deadline:
                break
            for sub in args.subs:
                walk_listing(f"/r/{sub}/new", {}, f"r/{sub}/new (poll)")
    except KeyboardInterrupt:
        log("Przerwano przez uzytkownika (Ctrl+C).")
    finally:
        save_state(state)
        log(f"=== Koniec pracy | Pobrane nowe save'y .cok: {total_saved} | Sprawdzonych postow: {len(state['seen_posts'])} ===")


if __name__ == "__main__":
    main()
