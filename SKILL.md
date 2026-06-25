---
name: ppt-to-imagesgallery
description: Convert a PPT file and its full manuscript into an imagesgallery output. Slide image rendering and validation use scripts; AI speech slicing/matching must be done directly in the current Codex session (not via script model calls).
---

# PPT To ImagesGallery

Use bundled scripts for deterministic file generation and validation.  
Do AI slicing/matching in the current session directly.

## BL Prerequisite Check (Required Before Workflow)

This skill uses Bailian CLI (`bl`) for TTS synthesis in the audio stage.
Before running the workflow, always do this pre-flight:

1. Check whether `bl` is available:

```bash
bl --version
```

2. If `bl` is missing, install it by following Aliyun official doc:
   `https://bailian.aliyun.com/cli/install.md`
   Use the documented install commands:

```bash
npm install -g bailian-cli
npx skills add modelstudioai/cli --all -g
bl --version
```

3. Check auth status:

```bash
bl auth status
```

4. If API key auth is not configured, ask user for Bailian API key, then login:

```bash
bl auth login --api-key <USER_API_KEY>
```

5. If user explicitly says to skip API key for now:
- Respect that choice.
- Continue all non-BL steps first (render images, in-session slicing, manifest validation).
- When reaching BL-dependent audio synthesis, pause and ask for API key again before running TTS.

## Quick Start

```bash
python3 scripts/build_imagesgallery.py \
  --ppt /path/to/slides.pptx \
  --speech /path/to/manuscript.docx \
  --out /path/to/output \
  --dry-run
```

Output directory:

- `/path/to/output/<ppt_name>/imagesgallery/images/page-001.png` ...
- `/path/to/output/<ppt_name>/imagesgallery/imagesgallery.json` (dry-run placeholder speech; overwrite in-session)

## Workflow

1. Run BL pre-flight check: install (if missing) and verify auth status.
2. Convert PPT to page images.
3. In current Codex session, match/slice manuscript to per-page speech in order.
4. Write final `imagesgallery.json` with real speech content.
5. Validate JSON shape and manuscript continuity.
6. Synthesize per-page speech to MP3, merge into one full MP3 with 1s gap between segments, and export continuous timestamps.
7. Generate `preview.html` in output for voiced PPT playback preview (auto page switch + subtitle sync).

## Command Interface

```bash
python3 scripts/build_imagesgallery.py \
  --ppt <file.ppt|file.pptx> \
  --speech <file.docx|file.md|file.txt> \
  --out <dir> \
  --dry-run
```

Arguments:

- `--ppt`: input slide deck.
- `--speech`: full manuscript that matches the PPT; supports `.docx`/`.md`/`.txt` (recommended: `.docx` from Word, including table content).
- `--out`: output base directory; script writes to `<out>/<ppt_name>/imagesgallery`.
- `--dry-run`: compatibility flag; script always generates images + manifest skeleton only.

## Ops Input Standard (Recommended)

For operation teams, use this stable workflow:

1. Prepare one PPT file and one full-script Word file (`.docx`) in the same folder.
2. Keep rich text in Word (headings, bold, bullet lists); do not export to plain `.txt`.
3. Run the skill with `--speech` pointing to the `.docx`.

Why:

- Plain `.txt` drops formatting.
- `.docx` preserves structure better, and the script converts major formatting to Markdown-like text for later subtitle rendering (headings, bold/italic, lists, and tables).

## Session Matching Rules

- Do not call `bl omni` for slicing.
- Use the current Codex session model to assign manuscript segments to each page.
- Use `references/prompt_full_speech_session.md` as the single source of slicing constraints.
- Run as full-deck one-shot slicing (all page images + full manuscript in one request).
- Model output must be strict JSON only:
  - `pages: [{page_number, speech}]`
- `page_number` must be continuous from `1..N`.

## Session Prompt Usage

Recommended process for better slicing accuracy:

1. Read `references/prompt_full_speech_session.md`.
2. Attach all page images in order (`page-001.png ... page-NNN.png`).
3. Provide the full manuscript in one request (not remaining-tail batches).
4. Ask model to return strict JSON only (no prose/code fence):
   - `pages: [{page_number, speech}]`
5. Post-check with `scripts/align_manuscript.py` and then write final manifest.

## Validation Rules

Use two-stage schema checks:

- Stage A (after session slicing): `imagesgallery.json` should contain
  - `version`,
  - `source_ppt`,
  - `source_speech`,
  - `items[{page_number,image,speech}]`.
- Stage B (after audio synthesis script rewrite): `imagesgallery.json` should contain
  - `version`,
  - `source_ppt`,
  - `source_speech`,
  - `audio`,
  - `items[{page,image,subtitle,start,end}]` where `start/end` are milliseconds.
- Continuity check must pass with `scripts/align_manuscript.py` (`strict_consume_pages`).
- Final manuscript tail must not contain substantive unconsumed text.

## Validation Snippet

Run after writing final JSON:

```bash
python3 - <<'PY'
import json
from pathlib import Path
import sys

root = Path(".codex/skills/ppt-to-imagesgallery/scripts").resolve()
sys.path.insert(0, str(root))
from align_manuscript import normalize_for_alignment, strict_consume_pages, is_substantive_gap
from build_imagesgallery import read_manuscript

manifest = Path("/path/to/output/<ppt_name>/imagesgallery/imagesgallery.json")
data = json.loads(manifest.read_text(encoding="utf-8"))
speech_path = Path(data["source_speech"])
manuscript = normalize_for_alignment(read_manuscript(speech_path))
# Supports both stage schemas:
pages = [
    item.get("speech", item.get("subtitle", ""))
    for item in data["items"]
]
res = strict_consume_pages(manuscript, pages, start_cursor=0)
tail = manuscript[res.cursor:]
if is_substantive_gap(tail):
    raise SystemExit("validation failed: unconsumed substantive tail")
print("OK: continuity validated")
PY
```

## Resources

- `scripts/build_imagesgallery.py`: rendering + manifest skeleton generation.
- `scripts/align_manuscript.py`: manuscript normalization and continuity checks.
- `scripts/synthesize_imagesgallery_audio.py`: per-segment TTS, merged MP3, and timeline timestamps.
- `references/prompt_full_speech_session.md`: default full-deck session slicing prompt.

## Audio Synthesis

After `imagesgallery.json` is finalized:

```bash
python3 scripts/synthesize_imagesgallery_audio.py \
  --manifest /path/to/output/<ppt_name>/imagesgallery/imagesgallery.json \
  --voice longxiaochun_v3 \
  --rate 1.1 \
  --gap-seconds 1 \
  --final-name full_speech.mp3 \
  --timeline-name speech_timestamps.json
```

Outputs:

- `/path/to/output/<ppt_name>/imagesgallery/audio/segments/page-001.mp3` ...
- `/path/to/output/<ppt_name>/imagesgallery/audio/full_speech.mp3`
- `/path/to/output/<ppt_name>/imagesgallery/audio/speech_timestamps.json`
- `/path/to/output/<ppt_name>/imagesgallery/preview.html`

Timestamp rule:

- Final manifest uses `items[i].start` / `end` in milliseconds.
- `end` includes trailing inter-segment gap for all non-last segments.
- Therefore adjacent items satisfy: `items[i].end == items[i+1].start`.

Preview behavior:

- Click play to start narration.
- Slide auto-switches by `items[].start/end` timestamps.
- Left thumbnail list supports click-to-seek.
- Subtitle panel shows current page `subtitle`.
