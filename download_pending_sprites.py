"""
Pulls the pendingSprites collection from MongoDB (sprites the bot resolved via
a wiki mirror rather than this repo's own github-hosted copy - see
DB_COLLECTION_PENDING_SPRITES in config.py and functions/utilities/getWikiSprite.py
for how entries get there) and attempts to download each one straight into
sprites/, reusing download_tibiawiki_assets.py's MediaWiki API lookup/download
helpers instead of duplicating them.

Unlike download_tibiawiki_assets.py (which scrapes whole listing pages to
discover name -> wiki-file pairs), each pendingSprites document already names
its target: gitName is the lowercase output filename (no extension) and
bossName is the wiki-style title (e.g. "Dworc_Shadowstalker") to look up
directly via the MediaWiki API.

    python download_pending_sprites.py

Read-only against Mongo - entries are never removed or modified, even once
downloaded, since this script only fills in sprites/ and leaves pending-list
bookkeeping to the bot.
"""
import argparse
import logging
import os
from pathlib import Path

import dotenv
import pymongo
from pymongo.server_api import ServerApi

import download_tibiawiki_assets as dta
from config import DB_COLLECTION_PENDING_SPRITES

log = logging.getLogger("pending_sprites")

SPRITES_DIR = Path(__file__).resolve().parent / "sprites"

# Extensions to check on disk before attempting a download, and to try (in
# order) against the wiki's MediaWiki API when resolving a source image -
# mirrors the mix of extensions actually present in sprites/ today.
CANDIDATE_EXTENSIONS = ["gif", "png", "jpg"]
LOOKUP_EXTENSIONS = ["gif", "png"]


def fetch_pending_sprites() -> list[dict]:
    root_env = dotenv.find_dotenv(usecwd=True)
    dotenv.load_dotenv(root_env)
    uri = os.getenv("MONGO_URI")
    db_name = os.getenv("DATABASE_NAME")
    client = pymongo.MongoClient(uri, server_api=ServerApi("1"))
    try:
        coll = client[db_name][DB_COLLECTION_PENDING_SPRITES]
        return list(coll.find({}))
    finally:
        client.close()


def existing_sprite(git_name: str) -> Path | None:
    for ext in CANDIDATE_EXTENSIONS:
        p = SPRITES_DIR / f"{git_name}.{ext}"
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def resolve_and_download(scraper, git_name: str, boss_name: str, rate_limiter, retries: int):
    for ext in LOOKUP_EXTENSIONS:
        wiki_file = f"{boss_name}.{ext}"
        try:
            src_url = dta.mw_original_image_url(scraper, wiki_file, rate_limiter, retries=retries)
        except Exception:
            log.exception("Failed to resolve source URL for '%s'", wiki_file)
            continue

        if not src_url:
            continue

        out_path = SPRITES_DIR / f"{git_name}.{ext}"
        try:
            dta.download(scraper, src_url, out_path, rate_limiter, retries=retries)
        except Exception:
            log.exception("Failed to download '%s' from %s", wiki_file, src_url)
            continue
        return out_path, src_url

    return None, None


def main():
    parser = argparse.ArgumentParser(
        description="Download every sprite listed in Mongo's pendingSprites collection into sprites/."
    )
    parser.add_argument("--delay", type=float, default=dta.REQUEST_DELAY_SECONDS,
                         help="Minimum seconds between wiki requests (default: %(default)s)")
    parser.add_argument("--retries", type=int, default=dta.MAX_RETRIES,
                         help="Max attempts per request before giving up (default: %(default)s)")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level),
                         format="%(asctime)s [%(levelname)s] %(message)s")

    docs = fetch_pending_sprites()
    log.info("Fetched %d pendingSprites entries from Mongo", len(docs))

    scraper = dta.cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    scraper.get(dta.WIKI_BASE + "/", timeout=60)
    rate_limiter = dta.RateLimiter(args.delay)

    downloaded = skipped = failed = 0
    for doc in docs:
        git_name = doc.get("gitName")
        boss_name = doc.get("bossName") or git_name
        if not git_name:
            continue

        existing = existing_sprite(git_name)
        if existing is not None:
            log.info("Skipping '%s' - already present (%s)", git_name, existing.name)
            skipped += 1
            continue

        out_path, src_url = resolve_and_download(scraper, git_name, boss_name, rate_limiter, args.retries)
        if out_path is not None:
            log.info("Downloaded '%s' -> %s", boss_name, out_path.name)
            downloaded += 1
        else:
            log.warning("No source found for '%s' (gitName=%s)", boss_name, git_name)
            failed += 1

    log.info("Done. Downloaded: %d | Already present: %d | Not found: %d", downloaded, skipped, failed)


if __name__ == "__main__":
    main()
