"""
Finds `_brwiki` sprite variants alongside their base sprite, classifies each
pair (byte-identical / pixel-identical / actually different), and builds an
interactive HTML gallery for reviewing the ones that differ.

Usage:
    python sprite_diff_tool.py                       # report + build gallery
    python sprite_diff_tool.py --delete-safe          # also delete byte- and
                                                        # pixel-identical
                                                        # _brwiki duplicates
    python sprite_diff_tool.py --sprites-dir sprites --out sprites/sprite_diff_gallery.html

Requires: Pillow, numpy (not in requirements.txt - install separately:
    pip install Pillow numpy
).
"""
import argparse
import filecmp
import json
import os
import sys

import numpy as np
from PIL import Image, ImageOps, ImageSequence

BRWIKI_SUFFIX = "_brwiki"


def find_pairs(sprites_dir):
    """(base_name, brwiki_name) for every *_brwiki.<ext> file with a matching base file."""
    pairs = []
    for entry in sorted(os.listdir(sprites_dir)):
        stem, ext = os.path.splitext(entry)
        if not stem.endswith(BRWIKI_SUFFIX):
            continue
        base_name = stem[: -len(BRWIKI_SUFFIX)] + ext
        if os.path.isfile(os.path.join(sprites_dir, base_name)):
            pairs.append((base_name, entry))
    return pairs


def load_frames_rgba(path):
    im = Image.open(path)
    return [fr.convert("RGBA") for fr in ImageSequence.Iterator(im)]


def is_pixel_identical(base_path, brwiki_path):
    try:
        return load_frames_rgba(base_path) == load_frames_rgba(brwiki_path)
    except Exception:
        return False


def classify_pairs(sprites_dir, pairs):
    byte_identical, pixel_identical, different = [], [], []
    for base_name, brwiki_name in pairs:
        base_path = os.path.join(sprites_dir, base_name)
        brwiki_path = os.path.join(sprites_dir, brwiki_name)
        if filecmp.cmp(base_path, brwiki_path, shallow=False):
            byte_identical.append((base_name, brwiki_name))
        elif is_pixel_identical(base_path, brwiki_path):
            pixel_identical.append((base_name, brwiki_name))
        else:
            different.append((base_name, brwiki_name))
    return byte_identical, pixel_identical, different


# ---- auto-generated "what differs" notes -----------------------------------

def dims_and_frames(path):
    try:
        frames = load_frames_rgba(path)
        w, h = frames[0].size
        return f"{w}x{h}", len(frames)
    except Exception:
        return "?", 0


def pixel_stats(arr):
    mask = arr[:, :, 3] > 10
    opaque = int(mask.sum())
    if opaque == 0:
        return {"opaque": 0, "brightness": 0.0, "colors": 0}
    rgb = arr[:, :, :3][mask]
    return {
        "opaque": opaque,
        "brightness": float(rgb.mean()),
        "colors": int(len(np.unique(rgb, axis=0))),
    }


def compare_metric(stats_a, stats_b, key, phrase, rel_tol=0.03):
    va, vb = stats_a[key], stats_b[key]
    if va == 0 and vb == 0:
        return None
    denom = max(va, vb, 1)
    if abs(va - vb) / denom < rel_tol:
        return None
    hi, lo = ("base", "_brwiki") if va > vb else ("_brwiki", "base")
    return f"{hi} {phrase} ({max(va, vb):.0f} vs {min(va, vb):.0f})"


def describe_diff(base_path, brwiki_path):
    """Best-effort, heuristic one-liner describing what differs between the two frames.

    Not a ground truth - just enough of a hint (size/frame-count mismatch,
    mirroring, small pixel shift, recolor vs. structural change) to triage
    464 pairs faster than eyeballing each one blind.
    """
    try:
        fb = load_frames_rgba(base_path)
        fw = load_frames_rgba(brwiki_path)
    except Exception as e:
        return f"could not analyze ({e})"

    notes = []
    if len(fb) != len(fw):
        notes.append(f"{len(fb)} vs {len(fw)} frames")

    a0, b0 = fb[0], fw[0]
    if a0.size != b0.size:
        area_a, area_b = a0.size[0] * a0.size[1], b0.size[0] * b0.size[1]
        bigger = "base" if area_a > area_b else "_brwiki"
        notes.append(f"canvas {a0.size[0]}x{a0.size[1]} vs {b0.size[0]}x{b0.size[1]} ({bigger} is larger)")
        return "; ".join(notes)

    arr_a = np.array(a0, dtype=np.int16)
    arr_b = np.array(b0, dtype=np.int16)
    total_px = arr_a.shape[0] * arr_a.shape[1]

    def pct_diff_of(other_arr):
        d = np.abs(arr_a - other_arr).sum(axis=2)
        return 100.0 * (d > 8).sum() / total_px

    pct_diff = pct_diff_of(arr_b)
    if pct_diff < 0.5:
        notes.append("near-identical (anti-aliasing/compression noise only)")
        return "; ".join(notes)

    arr_bf = np.array(ImageOps.mirror(b0), dtype=np.int16)
    if pct_diff_of(arr_bf) < min(pct_diff * 0.3, 5):
        notes.append("_brwiki appears horizontally mirrored")
        return "; ".join(notes)

    best_shift, best_pct = None, pct_diff
    for dx in range(-4, 5):
        for dy in range(-4, 5):
            if dx == 0 and dy == 0:
                continue
            shifted = np.roll(np.roll(arr_b, dx, axis=1), dy, axis=0)
            p = pct_diff_of(shifted)
            if p < best_pct:
                best_pct, best_shift = p, (dx, dy)
    if best_shift and best_pct < min(pct_diff * 0.3, 5):
        notes.append(f"_brwiki appears shifted ({best_shift[0]:+d},{best_shift[1]:+d})px")
        return "; ".join(notes)

    alpha_a = arr_a[:, :, 3] > 10
    alpha_b = arr_b[:, :, 3] > 10
    union = np.logical_or(alpha_a, alpha_b).sum()
    iou = (np.logical_and(alpha_a, alpha_b).sum() / union) if union else 1.0

    stats_a = pixel_stats(arr_a)
    stats_b = pixel_stats(arr_b)

    if iou > 0.85:
        note = f"same silhouette, different colors ({pct_diff:.0f}% of pixels)"
        extras = [
            compare_metric(stats_a, stats_b, "brightness", "is brighter"),
            compare_metric(stats_a, stats_b, "colors", "uses more distinct colors", rel_tol=0.1),
        ]
        extras = [e for e in extras if e]
        if extras:
            note += "; " + ", ".join(extras)
    else:
        note = f"different pose/shape ({pct_diff:.0f}% of pixels, silhouette overlap {iou*100:.0f}%)"
        extra = compare_metric(stats_a, stats_b, "opaque", "has more visible/opaque pixels (larger sprite)", rel_tol=0.1)
        if extra:
            note += "; " + extra
    notes.append(note)
    return "; ".join(notes)


# ---- gallery HTML ------------------------------------------------------------

def rel_uri(name):
    return name.replace(" ", "%20")


def build_gallery_html(sprites_dir, different_pairs, out_file):
    pairs_json, rows = [], []
    total = len(different_pairs)
    for i, (base_name, brwiki_name) in enumerate(different_pairs):
        base_path = os.path.join(sprites_dir, base_name)
        brwiki_path = os.path.join(sprites_dir, brwiki_name)

        base_dims, base_frames = dims_and_frames(base_path)
        brwiki_dims, brwiki_frames = dims_and_frames(brwiki_path)
        note = describe_diff(base_path, brwiki_path)

        pairs_json.append({"base": base_name, "brwiki": brwiki_name, "note": note})
        rows.append(f"""
    <div class="pair" data-key="{base_name}">
      <div class="badge resolved">&check; resolved</div>
      <div class="badge anomaly">&#9888; check manually</div>
      <div class="label">{base_name}</div>
      <div class="imgs">
        <div class="cell" data-choice="base">
          <img src="{rel_uri(base_name)}">
          <div class="cap">base &middot; {base_dims}{f' &middot; {base_frames}f' if base_frames else ''}</div>
        </div>
        <div class="cell" data-choice="brwiki">
          <img src="{rel_uri(brwiki_name)}">
          <div class="cap">_brwiki &middot; {brwiki_dims}{f' &middot; {brwiki_frames}f' if brwiki_frames else ''}</div>
        </div>
      </div>
      <div class="note">{note}</div>
      <div class="clear" title="clear decision">&times;</div>
    </div>""")
        if (i + 1) % 50 == 0:
            print(f"analyzed {i + 1}/{total}", file=sys.stderr)

    pairs_json_str = json.dumps(pairs_json)

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Sprite diff gallery</title>
<style>
body {{ font-family: system-ui, sans-serif; background:#1e1e1e; color:#ddd; margin:0; padding:16px; }}
h1 {{ font-size:18px; margin-bottom:4px; }}
.count {{ color:#999; margin-bottom:12px; }}
.warn {{ display:none; background:#4a3a1a; border:1px solid #8a6a2a; color:#e8c27a; padding:8px 12px; border-radius:4px; margin-bottom:12px; font-size:13px; }}
.toolbar {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:16px; }}
.toolbar input[type=text] {{ padding:6px; width:260px; }}
.toolbar label {{ font-size:13px; color:#bbb; display:flex; align-items:center; gap:4px; }}
button {{ background:#3a3a3a; color:#ddd; border:1px solid #555; border-radius:4px; padding:6px 10px; cursor:pointer; font-size:13px; }}
button:hover {{ background:#4a4a4a; }}
button.primary {{ background:#2f6f4f; border-color:#3f8f63; }}
button.primary:hover {{ background:#38835d; }}
button.danger {{ background:#6f2f2f; border-color:#8f3f3f; }}
button.danger:hover {{ background:#833838; }}
.grid {{ display:flex; flex-wrap:wrap; gap:12px; }}
.pair {{ position:relative; background:#2a2a2a; border-radius:6px; padding:8px; width:220px; border:2px solid transparent; }}
.pair.decided {{ border-color:#3f8f63; }}
.pair.resolved {{ border-color:#2a6b4a; opacity:0.55; }}
.pair.anomaly {{ border-color:#c0392b; }}
.badge {{ display:none; position:absolute; top:4px; left:6px; font-size:11px; padding:1px 6px; border-radius:8px; }}
.pair.resolved .badge.resolved {{ display:inline-block; background:#2a6b4a; color:#bfe8d2; }}
.pair.anomaly .badge.anomaly {{ display:inline-block; background:#c0392b; color:#ffd9d3; }}
.label {{ font-size:12px; word-break:break-all; margin-bottom:6px; color:#ccc; }}
.imgs {{ display:flex; gap:6px; }}
.cell {{ flex:1; text-align:center; background:#111; border-radius:4px; padding:4px; cursor:pointer; border:2px solid transparent; }}
.cell img {{ max-width:90px; max-height:90px; image-rendering:pixelated; background:
  repeating-conic-gradient(#333 0% 25%, #222 0% 50%) 50% / 12px 12px; }}
.cap {{ font-size:10px; color:#888; margin-top:2px; }}
.cell:hover {{ border-color:#666; }}
.cell.chosen {{ border-color:#4caf7d; background:#16281f; }}
.cell.chosen .cap {{ color:#7fd6a8; }}
.cell.rejected {{ opacity:0.35; }}
.note {{ font-size:10.5px; color:#c9a86a; margin-top:6px; line-height:1.3; min-height:2.6em; }}
.pair .clear {{ position:absolute; top:4px; right:6px; font-size:14px; color:#888; cursor:pointer; display:none; }}
.pair.decided .clear {{ display:block; }}
.pair .clear:hover {{ color:#f66; }}
.hint {{ font-size:11px; color:#777; margin-bottom:12px; }}
</style></head>
<body>
<h1>Sprite diff gallery &mdash; base vs _brwiki (pixel-different pairs)</h1>
<div class="count" id="count">{total} pairs</div>
<div class="warn" id="storageWarn">Heads up: this browser won't let a local file page save to localStorage, so your click decisions won't survive a reload/close. Use <b>Export decisions JSON</b> before closing the tab, and <b>Import decisions JSON</b> to resume later.</div>
<div class="toolbar">
  <input type="text" id="filter" placeholder="filter by name...">
  <label><input type="checkbox" id="undecidedOnly"> show undecided only</label>
  <label><input type="checkbox" id="hideResolved"> hide resolved (already applied on disk)</label>
  <button id="exportPs1" class="primary">Export PowerShell script</button>
  <button id="exportSh" class="primary">Export Bash script</button>
  <button id="exportJson">Export decisions JSON</button>
  <button id="importJson">Import decisions JSON</button>
  <input type="file" id="importFile" accept="application/json" style="display:none">
  <button id="clearAll" class="danger">Clear all decisions</button>
</div>
<div class="hint">Click a thumbnail to keep that version. Hover a pair and press 1 (keep base) / 2 (keep _brwiki) / 0 (clear). The tan note under each pair is an automated best-guess at what differs (size, frame count, mirroring, shift, recolor, or shape). Nothing on disk changes until you run an exported script &mdash; afterward, reload this page: pairs whose _brwiki file no longer exists are auto-flagged "resolved" (green), and the "hide resolved" checkbox filters them out.</div>
<div class="grid" id="grid">
{''.join(rows)}
</div>
<script>
const PAIRS = {pairs_json_str};
const STORAGE_KEY = 'spriteDiffDecisions_v1';
let decisions = {{}};
let storageBroken = false;

function loadDecisions() {{
  try {{
    decisions = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{{}}');
  }} catch (e) {{
    decisions = {{}};
  }}
}}
loadDecisions();

function save() {{
  try {{
    localStorage.setItem(STORAGE_KEY, JSON.stringify(decisions));
  }} catch (e) {{
    if (!storageBroken) {{
      storageBroken = true;
      document.getElementById('storageWarn').style.display = 'block';
    }}
  }}
}}

function countDecided() {{
  return Object.keys(decisions).filter(k => decisions[k]).length;
}}

function updateCountLabel() {{
  const resolved = document.querySelectorAll('.pair.resolved').length;
  const anomaly = document.querySelectorAll('.pair.anomaly').length;
  let text = `${{PAIRS.length}} pairs — ${{countDecided()}} decided, ${{PAIRS.length - countDecided()}} remaining`;
  if (resolved) text += ` — ${{resolved}} resolved on disk`;
  if (anomaly) text += ` — ${{anomaly}} unexpected (check manually)`;
  document.getElementById('count').textContent = text;
}}

function renderPair(pairEl) {{
  const key = pairEl.dataset.key;
  const choice = decisions[key];
  const cells = pairEl.querySelectorAll('.cell');
  pairEl.classList.toggle('decided', !!choice);
  cells.forEach(c => {{
    const isChosen = !!(choice && c.dataset.choice === choice);
    const isRejected = !!(choice && c.dataset.choice !== choice);
    c.classList.toggle('chosen', isChosen);
    c.classList.toggle('rejected', isRejected);
  }});
}}

function renderAll() {{
  document.querySelectorAll('.pair').forEach(renderPair);
  updateCountLabel();
}}

function setChoice(pairEl, choice) {{
  const key = pairEl.dataset.key;
  if (choice === null) {{
    delete decisions[key];
  }} else {{
    decisions[key] = choice;
  }}
  renderPair(pairEl);
  updateCountLabel();
  save();
}}

document.querySelectorAll('.pair').forEach(pairEl => {{
  renderPair(pairEl);
  pairEl.querySelectorAll('.cell').forEach(cell => {{
    cell.addEventListener('click', () => setChoice(pairEl, cell.dataset.choice));
  }});
  pairEl.querySelector('.clear').addEventListener('click', (e) => {{
    e.stopPropagation();
    setChoice(pairEl, null);
  }});

  // Detect whether the exported script has already been run for this pair by
  // checking which files actually load from disk right now.
  const baseImg = pairEl.querySelector('.cell[data-choice="base"] img');
  const brwikiImg = pairEl.querySelector('.cell[data-choice="brwiki"] img');
  const status = {{ base: null, brwiki: null }};
  function evalDiskStatus() {{
    if (status.base === null || status.brwiki === null) return;
    pairEl.classList.remove('resolved', 'anomaly');
    if (status.brwiki === 'error' && status.base === 'ok') {{
      pairEl.classList.add('resolved');
    }} else if (status.base === 'error') {{
      pairEl.classList.add('anomaly');
    }}
    updateCountLabel();
  }}
  function markLoad(which) {{ return () => {{ status[which] = 'ok'; evalDiskStatus(); }}; }}
  function markError(which) {{ return () => {{ status[which] = 'error'; evalDiskStatus(); }}; }}
  baseImg.addEventListener('load', markLoad('base'));
  baseImg.addEventListener('error', markError('base'));
  brwikiImg.addEventListener('load', markLoad('brwiki'));
  brwikiImg.addEventListener('error', markError('brwiki'));
  if (baseImg.complete) (baseImg.naturalWidth ? markLoad('base') : markError('base'))();
  if (brwikiImg.complete) (brwikiImg.naturalWidth ? markLoad('brwiki') : markError('brwiki'))();
}});
updateCountLabel();

let hovered = null;
document.getElementById('grid').addEventListener('mouseover', (e) => {{
  const p = e.target.closest('.pair');
  if (p) hovered = p;
}});
document.addEventListener('keydown', (e) => {{
  if (!hovered) return;
  if (e.target && (e.target.tagName === 'INPUT')) return;
  if (e.key === '1') setChoice(hovered, 'base');
  else if (e.key === '2') setChoice(hovered, 'brwiki');
  else if (e.key === '0' || e.key === 'Backspace') {{ e.preventDefault(); setChoice(hovered, null); }}
}});

document.getElementById('filter').addEventListener('input', function() {{
  const v = this.value.toLowerCase();
  const undecidedOnly = document.getElementById('undecidedOnly').checked;
  const hideResolved = document.getElementById('hideResolved').checked;
  document.querySelectorAll('.pair').forEach(p => {{
    const nameMatch = p.dataset.key.toLowerCase().includes(v);
    const decidedMatch = !undecidedOnly || !decisions[p.dataset.key];
    const resolvedMatch = !hideResolved || !p.classList.contains('resolved');
    p.style.display = (nameMatch && decidedMatch && resolvedMatch) ? '' : 'none';
  }});
}});
document.getElementById('undecidedOnly').addEventListener('change', function() {{
  document.getElementById('filter').dispatchEvent(new Event('input'));
}});
document.getElementById('hideResolved').addEventListener('change', function() {{
  document.getElementById('filter').dispatchEvent(new Event('input'));
}});

document.getElementById('clearAll').addEventListener('click', () => {{
  if (!confirm('Clear all ' + countDecided() + ' decisions?')) return;
  decisions = {{}};
  renderAll();
  save();
}});

function download(filename, text) {{
  const blob = new Blob([text], {{type: 'text/plain'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}}

function decidedPairs() {{
  return PAIRS.filter(p => decisions[p.base]).map(p => ({{...p, choice: decisions[p.base]}}));
}}

document.getElementById('exportJson').addEventListener('click', () => {{
  download('sprite_decisions.json', JSON.stringify(decisions, null, 2));
}});
document.getElementById('importJson').addEventListener('click', () => {{
  document.getElementById('importFile').click();
}});
document.getElementById('importFile').addEventListener('change', (e) => {{
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {{
    try {{
      const imported = JSON.parse(reader.result);
      decisions = imported || {{}};
      renderAll();
      save();
      alert('Imported ' + countDecided() + ' decisions.');
    }} catch (err) {{
      alert('Could not parse that file as decisions JSON.');
    }}
  }};
  reader.readAsText(file);
  e.target.value = '';
}});

function ps1Quote(s) {{
  return "'" + s.replace(/'/g, "''") + "'";
}}
function shQuote(s) {{
  return "'" + s.replace(/'/g, "'\\\\''") + "'";
}}

document.getElementById('exportPs1').addEventListener('click', () => {{
  const items = decidedPairs();
  if (!items.length) {{ alert('No decisions made yet.'); return; }}
  const lines = [
    '# Auto-generated by sprite_diff_gallery.html',
    '# Run this from inside the sprites/ folder.',
    '# For each decided pair: keep-base deletes the _brwiki file;',
    '# keep-brwiki deletes the base file and renames _brwiki to the base name.',
    'Set-StrictMode -Version Latest',
    '$ErrorActionPreference = "Stop"',
    ''
  ];
  for (const p of items) {{
    if (p.choice === 'base') {{
      lines.push(`Remove-Item -LiteralPath ${{ps1Quote(p.brwiki)}} -Force`);
    }} else {{
      lines.push(`Remove-Item -LiteralPath ${{ps1Quote(p.base)}} -Force`);
      lines.push(`Rename-Item -LiteralPath ${{ps1Quote(p.brwiki)}} -NewName ${{ps1Quote(p.base)}}`);
    }}
  }}
  lines.push('', `Write-Host "Applied ${{items.length}} decisions."`);
  download('apply_sprite_decisions.ps1', lines.join('\\r\\n'));
}});

document.getElementById('exportSh').addEventListener('click', () => {{
  const items = decidedPairs();
  if (!items.length) {{ alert('No decisions made yet.'); return; }}
  const lines = [
    '#!/usr/bin/env bash',
    '# Auto-generated by sprite_diff_gallery.html',
    '# Run this from inside the sprites/ folder.',
    'set -euo pipefail',
    ''
  ];
  for (const p of items) {{
    if (p.choice === 'base') {{
      lines.push(`rm -f -- ${{shQuote(p.brwiki)}}`);
    }} else {{
      lines.push(`rm -f -- ${{shQuote(p.base)}}`);
      lines.push(`mv -- ${{shQuote(p.brwiki)}} ${{shQuote(p.base)}}`);
    }}
  }}
  lines.push('', `echo "Applied ${{items.length}} decisions."`);
  download('apply_sprite_decisions.sh', lines.join('\\n'));
}});
</script>
</body></html>
"""

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sprites-dir", default="sprites")
    parser.add_argument("--out", default=None, help="Gallery HTML output path (default: <sprites-dir>/sprite_diff_gallery.html)")
    parser.add_argument("--delete-safe", action="store_true", help="Delete _brwiki files that are byte- or pixel-identical to their base (safe, no visual difference).")
    args = parser.parse_args()

    sprites_dir = args.sprites_dir
    out_file = args.out or os.path.join(sprites_dir, "sprite_diff_gallery.html")

    pairs = find_pairs(sprites_dir)
    print(f"{len(pairs)} _brwiki files have a matching base sprite")

    byte_identical, pixel_identical, different = classify_pairs(sprites_dir, pairs)
    print(f"  byte-identical:  {len(byte_identical)}")
    print(f"  pixel-identical (byte diff only, e.g. metadata): {len(pixel_identical)}")
    print(f"  actually different: {len(different)}")

    if args.delete_safe:
        for base_name, brwiki_name in byte_identical + pixel_identical:
            os.remove(os.path.join(sprites_dir, brwiki_name))
        print(f"Deleted {len(byte_identical) + len(pixel_identical)} redundant _brwiki files.")

    print(f"Building gallery for {len(different)} differing pairs -> {out_file}")
    build_gallery_html(sprites_dir, different, out_file)
    print("Done.")


if __name__ == "__main__":
    main()
