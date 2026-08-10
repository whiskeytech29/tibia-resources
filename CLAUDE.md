# tibia-resources

Sprite resources for Tibia bots. `sprites/` holds the actual sprite GIFs; the
root scripts are one-off/utility tooling for maintaining that folder.

`.gitignore` excludes `__pycache__/` and other compiled Python artifacts.

## `download_tibiawiki_assets.py` (repo root)

Scrapes creature/boss listing pages from the Portuguese TibiaWiki
(tibiawiki.com.br), resolves each entry's original image via the MediaWiki
API, and downloads it under a Fandom-style, all-lowercase filename (e.g.
`the_voice_of_ruin.gif`). This is the pipeline that originally populated
`sprites/` and is the source of the `_brwiki` dupes described below.
Requires `cloudscraper` + `beautifulsoup4` + `requests` (listed in
`requirements.txt`).

```
python download_tibiawiki_assets.py                            # bosses + all creature categories, defaults below
python download_tibiawiki_assets.py --groups bosses             # only the bosses page
python download_tibiawiki_assets.py --out-dir ./out --workers 8 --delay 0.05
```

Defaults when run with no args:
- `--out-dir`: the OS Desktop's `TibiaSprites` subfolder, resolved from the
  home directory at runtime (not a fixed path)
- `--groups`: `all` (bosses + every creature category page)
- `--workers`: `4` concurrent download threads
- `--delay`: `0.08`s minimum spacing between requests, enforced *globally*
  across all worker threads (not per-thread) via a shared rate limiter
- `--retries`: `3` attempts per HTTP request (page fetch, MediaWiki API
  lookup, image download), with exponential backoff, before giving up on
  that item
- `--log-level`: `INFO`, logged to both the console and a `download.log`
  file inside the output directory

Behavior notes:
- Output layout: `<out-dir>/bosses/` and `<out-dir>/creatures/`, each with
  the downloaded image files plus an `index.json` array of
  `{name, file, sourceWikiFile, sourceUrl, downloaded}` entries.
- `index.json` is written immediately after the listing scrape (before any
  downloads start) and re-saved after every single item completes, so a
  crash mid-run only loses the one in-flight item, not the whole group.
- Already-downloaded files (non-empty, present on disk) are skipped without
  hitting the network again on reruns — safe to re-run to top up a partial
  download.
- Output filenames are lowercased and stripped of characters illegal in
  Windows/Unix paths; a warning is logged if two different wiki entity names
  collide to the same output filename.

### Merging a fresh download into `sprites/`

The script's output folder (`<out-dir>/bosses/` and `<out-dir>/creatures/`)
is separate from `sprites/` — nothing copies it over automatically. When
merging one in by hand: for each file, copy it to `sprites/` under its
original name if that name isn't already taken; if it is, copy it in as
`<name>_brwiki.<ext>` instead of overwriting. If *that* name is also already
taken, leave it out — it's already represented on both sides. This is the
same collision rule that originally produced the `_brwiki` dupes described
below, so re-merging a download is always non-destructive to what's already
in `sprites/`.

## `_brwiki` duplicate sprites

Many files in `sprites/` have a second copy suffixed `_brwiki`
(e.g. `foo.gif` and `foo_brwiki.gif`) picked up during scraping. Some are
byte-for-byte duplicates of the base file, some are pixel-identical despite
differing bytes (GIF metadata/palette-order only), and some are genuinely
different images (different pose/animation frame, recolor, mirrored, shifted,
or a different canvas size) — those need a human decision on which version to
keep under the base filename.

### `sprite_diff_tool.py` (repo root)

Scans a sprites directory for `*_brwiki.<ext>` files that have a matching
base file, classifies each pair as byte-identical / pixel-identical /
actually different, and (re)builds an interactive HTML gallery for the
"actually different" set. Requires Pillow + numpy (listed in
`requirements.txt`).

```
python sprite_diff_tool.py                  # report counts, rebuild the gallery, no deletions
python sprite_diff_tool.py --delete-safe     # also delete byte-/pixel-identical _brwiki dupes
```

Re-run it any time — including after applying a cleanup script (see below) —
to refresh the gallery down to whatever pairs are still actually left.

### `sprites/sprite_diff_gallery.html` (generated — don't hand-edit)

Side-by-side viewer for every remaining differing pair, with an
auto-generated one-line note guessing what differs (frame-count mismatch,
canvas-size mismatch, near-identical/noise-only, horizontal mirror, small
pixel shift, "same silhouette but recolored" — with which side is brighter /
has more colors — or "different pose/shape" — with which side has more
visible pixels). The notes are a heuristic triage aid, not ground truth.

It's fully static and self-contained but **not** portable on its own: images
are referenced by relative path to the real files in `sprites/`, not
embedded, so the HTML must stay in that folder to render. This is
deliberate — reloading the page always reflects whatever is currently on
disk, which the "resolved" detection below relies on.

Workflow:
1. Click a thumbnail to mark it "keep" (or hover a pair and press `1` for
   base / `2` for `_brwiki` / `0` to clear).
2. Decisions are best-effort saved to `localStorage` — some browsers throw a
   SecurityError for `localStorage` on `file://` pages (opaque origin); a
   warning banner appears if that happens, and "Export/Import decisions
   JSON" exist as a manual fallback so progress isn't lost.
3. "Export PowerShell script" / "Export Bash script" turns current decisions
   into an apply script: keep-base deletes the `_brwiki` file; keep-brwiki
   deletes the base file and renames `_brwiki` to the base filename. Run the
   downloaded script from inside `sprites/`. The page itself never touches
   the filesystem — nothing changes until that script runs.
4. After running the script, reload the gallery: any pair whose `_brwiki`
   file no longer loads is auto-flagged "resolved" (green badge), with a
   "hide resolved" filter checkbox to focus on what's left. (Re-running
   `sprite_diff_tool.py` achieves the same thing more thoroughly, since it
   rebuilds the pair list from scratch.)

### Gotchas

- `localStorage.setItem` can throw synchronously on `file://` pages in some
  browsers; the gallery's JS renders the UI *before* calling `save()` and
  catches failures, specifically so a storage error can't silently break the
  click-to-select UI (this was a real bug once — decisions updated in memory
  but the screen never repainted, which looked like random state).
- The diff notes are pixel-heuristics (frame-exact comparison, alpha-mask
  IoU for silhouette overlap, translation/mirror search within a few pixels)
  — good for triage, not a substitute for actually looking at the thumbnail.
