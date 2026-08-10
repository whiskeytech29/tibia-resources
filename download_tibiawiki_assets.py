import argparse
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse, unquote

import cloudscraper
import requests
from bs4 import BeautifulSoup

WIKI_BASE = "https://www.tibiawiki.com.br"
API_URL = f"{WIKI_BASE}/api.php"
REQUEST_DELAY_SECONDS = 0.08
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.5
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

log = logging.getLogger("tibiawiki_assets")

BOSSES_PAGE = f"{WIKI_BASE}/wiki/Bosses"

CREATURE_PAGES = [
    f"{WIKI_BASE}/wiki/Anf%C3%ADbios",
    f"{WIKI_BASE}/wiki/Aqu%C3%A1ticos",
    f"{WIKI_BASE}/wiki/Aves",
    f"{WIKI_BASE}/wiki/Constructos",
    f"{WIKI_BASE}/wiki/Criaturas_M%C3%A1gicas",
    f"{WIKI_BASE}/wiki/Dem%C3%B4nios",
    f"{WIKI_BASE}/wiki/Drag%C3%B5es",
    f"{WIKI_BASE}/wiki/Elementais",
    f"{WIKI_BASE}/wiki/Extra_Dimensionais",
    f"{WIKI_BASE}/wiki/Fadas",
    f"{WIKI_BASE}/wiki/Gigantes",
    f"{WIKI_BASE}/wiki/Humanos",
    f"{WIKI_BASE}/wiki/Human%C3%B3ides",
    f"{WIKI_BASE}/wiki/Imortais",
    f"{WIKI_BASE}/wiki/Inkborn",
    f"{WIKI_BASE}/wiki/Licantropos",
    f"{WIKI_BASE}/wiki/Mam%C3%ADferos",
    f"{WIKI_BASE}/wiki/Mortos-Vivos",
    f"{WIKI_BASE}/wiki/Plantas_(Criatura)",
    f"{WIKI_BASE}/wiki/R%C3%A9pteis",
    f"{WIKI_BASE}/wiki/Slimes",
    f"{WIKI_BASE}/wiki/Vermes",
]


def desktop_path() -> Path:
    if os.name == "nt" and os.environ.get("USERPROFILE"):
        p = Path(os.environ["USERPROFILE"]) / "Desktop"
        if p.exists():
            return p
    return Path.home() / "Desktop"


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


class RateLimiter:
    """
    Thread-safe global throttle: guarantees at least `min_interval` seconds
    between the *start* of any two requests, no matter how many worker
    threads are issuing them, so concurrency doesn't hammer the wiki harder
    than a single sequential run would.
    """

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next_time = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_time - now
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_time = now + self.min_interval


_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """
    Strip characters that are illegal in Windows/Unix filenames (and could
    otherwise cause a wiki entity name to write outside the target directory).
    """
    cleaned = _INVALID_FS_CHARS.sub("_", name).strip(" .")
    return cleaned


def build_fandom_gif_file(name: str) -> str | None:
    """
    Python port of your Node buildWikiGifFile().
    Produces "the_voice_of_ruin.gif" style names (lowercase for the output file).
    """
    if not name:
        return None

    # normalize whitespace
    s = " ".join(str(name).strip().split())

    LOWER = {"a", "an", "the", "of", "in", "on", "to", "and", "with", "from"}

    words = s.split(" ")
    out_words = []
    for i, w in enumerate(words):
        segs = w.split("-")
        segs2 = []
        for seg in segs:
            if not seg:
                segs2.append(seg)
                continue
            segs2.append(seg[:1].upper() + seg[1:].lower())
        out = "-".join(segs2)
        if i > 0 and out.lower() in LOWER:
            out = out.lower()
        out_words.append(out)

    s2 = " ".join(out_words).replace(" ", "_")
    s2 = sanitize_filename(s2)
    if not s2:
        return None
    return f"{s2}.gif".lower()


def get_with_retry(scraper, url: str, rate_limiter: RateLimiter, *, retries: int = MAX_RETRIES, **kwargs):
    """
    GET with exponential backoff on connection errors and retryable HTTP
    status codes (429/5xx). Non-retryable status codes (e.g. 403, 404) are
    returned as-is so callers can inspect them (fetch_html relies on 403).
    """
    last_exc: Exception | None = None
    r = None
    for attempt in range(1, retries + 1):
        rate_limiter.wait()
        try:
            r = scraper.get(url, **kwargs)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt == retries:
                raise
            sleep_for = RETRY_BACKOFF_SECONDS ** attempt
            log.warning("Request error on %s (%s); retrying in %.1fs", url, exc, sleep_for)
            time.sleep(sleep_for)
            continue

        if r.status_code in RETRYABLE_STATUS_CODES and attempt < retries:
            sleep_for = RETRY_BACKOFF_SECONDS ** attempt
            log.warning("HTTP %s on %s; retrying in %.1fs", r.status_code, url, sleep_for)
            time.sleep(sleep_for)
            continue

        return r

    if last_exc:
        raise last_exc
    return r


def fetch_html(scraper, url: str, rate_limiter: RateLimiter) -> str:
    """
    Try direct page; if blocked, fall back to API parse.
    """
    r = get_with_retry(scraper, url, rate_limiter, timeout=60)
    if r.status_code != 403:
        r.raise_for_status()
        return r.text

    # Fallback via parse API using the page title from /wiki/<Title>
    path = urlparse(url).path
    title = unquote(path.split("/wiki/")[-1])

    params = {
        "action": "parse",
        "page": title,
        "prop": "text",
        "format": "json",
        "redirects": "1",
        "origin": "*",
    }
    api_r = get_with_retry(scraper, API_URL, rate_limiter, params=params, timeout=60)
    api_r.raise_for_status()
    data = api_r.json()
    html = (data.get("parse", {}).get("text", {}) or {}).get("*")
    if not html:
        raise RuntimeError(f"Could not parse HTML for page '{title}'.")
    return html


def mw_original_image_url(
    scraper, filename: str, rate_limiter: RateLimiter, *, retries: int = MAX_RETRIES
) -> str | None:
    """
    MediaWiki API lookup to get original file URL.
    Tries Portuguese namespace 'Arquivo:' then 'File:' fallback.
    """
    for ns in ("Arquivo:", "File:"):
        params = {
            "action": "query",
            "titles": f"{ns}{filename}",
            "prop": "imageinfo",
            "iiprop": "url",
            "format": "json",
            "origin": "*",
        }
        r = get_with_retry(scraper, API_URL, rate_limiter, params=params, timeout=60, retries=retries)
        if r.status_code == 403:
            return None
        r.raise_for_status()
        data = r.json()

        pages = data.get("query", {}).get("pages", {})
        for _, page in pages.items():
            ii = page.get("imageinfo")
            if ii and isinstance(ii, list) and ii[0].get("url"):
                return ii[0]["url"]

    return None


def download(
    scraper, url: str, out_path: Path, rate_limiter: RateLimiter, *, retries: int = MAX_RETRIES
) -> None:
    """
    Stream a file to disk, retrying the whole transfer on connection errors.
    Any partially-written file from a failed attempt is removed before retrying.
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        rate_limiter.wait()
        try:
            with scraper.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(out_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 128):
                        if chunk:
                            f.write(chunk)
            return
        except (requests.exceptions.RequestException, OSError) as exc:
            last_exc = exc
            out_path.unlink(missing_ok=True)
            if attempt == retries:
                raise
            sleep_for = RETRY_BACKOFF_SECONDS ** attempt
            log.warning("Download error on %s (%s); retrying in %.1fs", url, exc, sleep_for)
            time.sleep(sleep_for)
    if last_exc:
        raise last_exc


def extract_name_and_wiki_file(html: str) -> list[tuple[str, str]]:
    """
    Extract (entity_name, wiki_filename) by scanning rows with an <img alt="X.gif/png">
    and a sensible first wiki link in the row.
    """
    soup = BeautifulSoup(html, "html.parser")
    pairs: list[tuple[str, str]] = []

    for tr in soup.find_all("tr"):
        img = tr.find("img", alt=True)
        if not img:
            continue

        wiki_file = (img.get("alt") or "").strip()
        if not re.search(r"\.(gif|png)$", wiki_file, re.IGNORECASE):
            continue

        chosen = None
        for a in tr.find_all("a", href=True):
            title = (a.get("title") or "").strip()
            href = a["href"]
            text = a.get_text(strip=True)

            if not text:
                continue
            if title.startswith(("Arquivo:", "Image:", "Especial:", "Special:")):
                continue
            if "/wiki/" not in href:
                continue

            chosen = text
            break

        if not chosen:
            continue

        pairs.append((chosen, wiki_file))

    return pairs


def save_index(index: dict, out_index: Path) -> None:
    """Write index.json as an array (easier for JS to consume)."""
    with open(out_index, "w", encoding="utf-8") as f:
        json.dump(list(index.values()), f, ensure_ascii=False, indent=2)


def process_item(
    scraper, name: str, row: dict, sprites_dir: Path, rate_limiter: RateLimiter, retries: int
) -> tuple[str, str, str | None]:
    """
    Resolve and download a single sprite. Runs on a worker thread; touches only
    its own `row`, never shared index/counter state, so callers must apply the
    returned status themselves. Returns (name, status, source_url) where status
    is one of "present" / "downloaded" / "failed".
    """
    out_path = sprites_dir / row["file"]
    if out_path.exists() and out_path.stat().st_size > 0:
        return name, "present", None

    try:
        src_url = mw_original_image_url(scraper, row["sourceWikiFile"], rate_limiter, retries=retries)
    except Exception:
        log.exception("Failed to resolve source URL for '%s' (%s)", name, row["sourceWikiFile"])
        src_url = None

    if not src_url:
        log.warning("No source URL found for '%s' (%s); skipping", name, row["sourceWikiFile"])
        return name, "failed", None

    try:
        download(scraper, src_url, out_path, rate_limiter, retries=retries)
        log.info("Downloaded '%s' -> %s", name, row["file"])
        return name, "downloaded", src_url
    except Exception:
        log.exception("Failed to download '%s' from %s", name, src_url)
        return name, "failed", src_url


def run_group(
    scraper,
    group_name: str,
    page_urls: list[str],
    out_base: Path,
    rate_limiter: RateLimiter,
    workers: int,
    retries: int,
) -> None:
    group_dir = out_base / group_name
    sprites_dir = group_dir
    ensure_dir(sprites_dir)
    out_index = group_dir / "index.json"

    # name -> { name, file, sourceWikiFile, sourceUrl, downloaded }
    # file is the Fandom-style .gif filename you will host in GitHub
    index: dict[str, dict] = {}
    files_seen: dict[str, str] = {}  # fandom_file -> first name that claimed it

    log.info("=== %s ===", group_name.upper())
    for url in page_urls:
        log.info("Fetching: %s", url)
        html = fetch_html(scraper, url, rate_limiter)
        pairs = extract_name_and_wiki_file(html)
        log.info("  Found %d rows", len(pairs))

        for name, wiki_file in pairs:
            if name in index:
                continue

            fandom_file = build_fandom_gif_file(name)
            if not fandom_file:
                continue

            prior_owner = files_seen.get(fandom_file)
            if prior_owner and prior_owner != name:
                log.warning(
                    "Filename collision for '%s': '%s' and '%s' both map to %s",
                    group_name, prior_owner, name, fandom_file,
                )
            files_seen[fandom_file] = name

            index[name] = {
                "name": name,
                "file": fandom_file,
                "sourceWikiFile": wiki_file,
                "sourceUrl": "",
                "downloaded": False,
            }

    log.info("Total unique %s: %d", group_name, len(index))
    # Persist the listing immediately, before any downloads start, so a crash
    # mid-download doesn't lose the (slow to re-scrape) name/file mapping.
    save_index(index, out_index)

    downloaded = 0
    already_present = 0
    failed = 0

    # Download each sprite using the TibiaWiki file, but SAVE AS your Fandom-style filename.
    # Workers only touch their own row; all index/counter/file bookkeeping happens
    # here on the main thread as each future completes, so no locking is needed.
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(process_item, scraper, name, row, sprites_dir, rate_limiter, retries): name
            for name, row in index.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            row = index[name]
            _, status, src_url = future.result()

            if src_url:
                row["sourceUrl"] = src_url
            if status == "present":
                already_present += 1
                row["downloaded"] = True
            elif status == "downloaded":
                downloaded += 1
                row["downloaded"] = True
            else:
                failed += 1

            save_index(index, out_index)

    log.info("Saved: %s", group_dir)
    log.info(
        "Downloaded: %d | Already present: %d | Failed: %d",
        downloaded, already_present, failed,
    )
    log.info("Index: %s", out_index)


def configure_logging(out_base: Path, level_name: str) -> None:
    log_path = out_base / "download.log"
    logging.basicConfig(
        level=getattr(logging, level_name),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download creature/boss sprites from TibiaWiki (pt-BR) and build a Fandom-style index.json."
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None,
        help="Output directory (default: <Desktop>/TibiaSprites)",
    )
    parser.add_argument(
        "--groups", choices=["bosses", "creatures", "all"], default="all",
        help="Which group(s) to download (default: %(default)s)",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Concurrent download workers (default: %(default)s)",
    )
    parser.add_argument(
        "--delay", type=float, default=REQUEST_DELAY_SECONDS,
        help="Minimum seconds between requests, shared across all workers (default: %(default)s)",
    )
    parser.add_argument(
        "--retries", type=int, default=MAX_RETRIES,
        help="Max attempts per request before giving up (default: %(default)s)",
    )
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO",
        help="Logging verbosity (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()

    out_base = args.out_dir or (desktop_path() / "TibiaSprites")
    ensure_dir(out_base)
    configure_logging(out_base, args.log_level)

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    scraper.get(WIKI_BASE + "/", timeout=60)

    rate_limiter = RateLimiter(args.delay)

    if args.groups in ("bosses", "all"):
        run_group(scraper, "bosses", [BOSSES_PAGE], out_base, rate_limiter, args.workers, args.retries)
    if args.groups in ("creatures", "all"):
        run_group(scraper, "creatures", CREATURE_PAGES, out_base, rate_limiter, args.workers, args.retries)

    log.info("All done.")
    log.info("Output root: %s", out_base)


if __name__ == "__main__":
    main()