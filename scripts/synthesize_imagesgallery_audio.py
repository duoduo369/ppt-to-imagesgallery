#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Sequence


def resolve_bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    fallback = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/bin" / name
    if fallback.exists():
        return str(fallback)
    raise FileNotFoundError(f"required executable not found: {name}")


def run_cmd(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        list(cmd),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "command failed:\n"
            f"cmd: {' '.join(cmd)}\n"
            f"code: {proc.returncode}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}\n"
        )
    return proc


def ffprobe_duration(ffprobe_bin: str, audio_path: Path) -> float:
    proc = run_cmd(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]
    )
    try:
        return float(proc.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"unable to parse duration for {audio_path}: {proc.stdout!r}") from exc


def to_ts(seconds: float) -> str:
    ms_total = int(round(seconds * 1000))
    h = ms_total // 3_600_000
    ms_total -= h * 3_600_000
    m = ms_total // 60_000
    ms_total -= m * 60_000
    s = ms_total // 1000
    ms = ms_total - s * 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def to_ms(seconds: float) -> int:
    return int(round(seconds * 1000))


def to_rel_url(target: str, base_dir: Path) -> str:
    p = Path(target)
    if p.is_absolute():
        rel = os.path.relpath(str(p), str(base_dir))
        return rel.replace("\\", "/")
    return str(p).replace("\\", "/")


def build_preview_html(manifest: Dict[str, object], manifest_path: Path) -> Path:
    out_html = manifest_path.parent / "preview.html"
    skill_root = Path(__file__).resolve().parents[1]
    template_path = skill_root / "references" / "preview_template.html"
    if not template_path.exists():
        raise FileNotFoundError(f"preview template not found: {template_path}")

    items = manifest.get("items", [])
    if not isinstance(items, list) or not items:
        raise ValueError("manifest items must be a non-empty list for preview generation")

    normalized_items = []
    for item in items:
        page = int(item.get("page", item.get("page_number")))
        subtitle = str(item.get("subtitle", item.get("speech", "")))
        start = int(item.get("start", to_ms(float(item.get("start_seconds", 0)))))
        end = int(item.get("end", to_ms(float(item.get("end_seconds", 0)))))
        image = str(item.get("image", ""))
        normalized_items.append(
            {
                "page": page,
                "image": image,
                "subtitle": subtitle,
                "start": start,
                "end": end,
            }
        )
    normalized_items.sort(key=lambda x: x["page"])

    audio = manifest.get("audio", {}) if isinstance(manifest.get("audio"), dict) else {}
    full_audio = str(audio.get("full_audio", ""))
    if not full_audio:
        raise ValueError("manifest.audio.full_audio is required for preview generation")

    payload = {
        "items": normalized_items,
        "audio": {
            "rate": audio.get("rate", 1.0),
        },
    }
    title = f"{Path(str(manifest.get('source_ppt', 'PPT'))).stem} - 有声预览"
    rendered = (
        template_path.read_text(encoding="utf-8")
        .replace("__TITLE__", title)
        .replace("__AUDIO_SRC__", to_rel_url(full_audio, out_html.parent))
        .replace("__DATA_JSON__", json.dumps(payload, ensure_ascii=False))
    )
    out_html.write_text(rendered, encoding="utf-8")
    return out_html


def build_audio(args: argparse.Namespace) -> Dict[str, object]:
    manifest_path = Path(args.manifest).expanduser().resolve()
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("manifest items must be a non-empty array")

    sorted_items = sorted(items, key=lambda it: int(it.get("page_number", it.get("page"))))
    expected = list(range(1, len(sorted_items) + 1))
    got = [int(it.get("page_number", it.get("page"))) for it in sorted_items]
    if got != expected:
        raise ValueError(f"page_number must be continuous from 1..N; got={got[:10]}...")

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else manifest_path.parent / "audio"
    segments_dir = out_dir / "segments"
    out_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)
    texts_dir = out_dir / "texts"
    texts_dir.mkdir(parents=True, exist_ok=True)

    bl_bin = resolve_bin("bl")
    ffmpeg_bin = resolve_bin("ffmpeg")
    ffprobe_bin = resolve_bin("ffprobe")

    segment_rows: List[Dict[str, object]] = []
    for item in sorted_items:
        page_number = int(item.get("page_number", item.get("page")))
        speech = str(item.get("speech", item.get("subtitle", ""))).strip()
        if not speech:
            raise ValueError(f"empty speech on page {page_number}")

        seg_path = segments_dir / f"page-{page_number:03d}.mp3"
        if not (args.skip_existing and seg_path.exists()):
            text_file = texts_dir / f"page-{page_number:03d}.txt"
            text_file.write_text(speech, encoding="utf-8")
            cmd = [
                bl_bin,
                "speech",
                "synthesize",
                "--voice",
                args.voice,
                "--rate",
                str(args.rate),
                "--text-file",
                str(text_file),
                "--format",
                "mp3",
                "--out",
                str(seg_path),
                "--quiet",
                "--output",
                "json",
            ]
            if args.model:
                cmd.extend(["--model", args.model])
            if args.language:
                cmd.extend(["--language", args.language])
            run_cmd(cmd)
            if not seg_path.exists():
                raise RuntimeError(f"tts did not generate segment audio: {seg_path}")

        duration = ffprobe_duration(ffprobe_bin, seg_path)
        segment_rows.append(
            {
                "page_number": page_number,
                "image": item.get("image"),
                "speech": speech,
                "segment_audio": str(seg_path),
                "duration_seconds": round(duration, 3),
            }
        )

    gap_seconds = float(args.gap_seconds)
    if gap_seconds < 0:
        raise ValueError("--gap-seconds must be >= 0")

    silence_path = out_dir / f"gap-{int(round(gap_seconds * 1000)):04d}ms.mp3"
    if gap_seconds > 0 and len(segment_rows) > 1:
        run_cmd(
            [
                ffmpeg_bin,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-t",
                str(gap_seconds),
                "-acodec",
                "libmp3lame",
                "-q:a",
                "9",
                str(silence_path),
            ]
        )

    concat_list = out_dir / "concat_inputs.txt"
    with concat_list.open("w", encoding="utf-8") as f:
        for idx, row in enumerate(segment_rows):
            f.write(f"file '{row['segment_audio']}'\n")
            if gap_seconds > 0 and idx < len(segment_rows) - 1:
                f.write(f"file '{silence_path}'\n")

    final_audio = out_dir / args.final_name
    run_cmd(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(final_audio),
        ]
    )

    cursor = 0.0
    for idx, row in enumerate(segment_rows):
        start = cursor
        end = start + float(row["duration_seconds"])
        row["start_ms"] = to_ms(start)
        row["end_ms"] = to_ms(end)
        row["start_seconds"] = round(start, 3)
        row["end_seconds"] = round(end, 3)
        row["start_timestamp"] = to_ts(start)
        row["end_timestamp"] = to_ts(end)
        cursor = end
        if gap_seconds > 0 and idx < len(segment_rows) - 1:
            cursor += gap_seconds

    timeline = {
        "version": "1.0",
        "source_manifest": str(manifest_path),
        "voice": args.voice,
        "rate": float(args.rate),
        "model": args.model or "",
        "gap_seconds_between_segments": gap_seconds,
        "final_audio": str(final_audio),
        "total_duration_seconds": round(cursor, 3),
        "items": segment_rows,
    }

    timeline_path = out_dir / args.timeline_name
    timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")

    # Rewrite imagesgallery.json with consumer schema:
    # page/subtitle/start/end(ms), replacing page_number/speech/start_seconds/end_seconds.
    rewritten_items: List[Dict[str, object]] = []
    gap_ms = to_ms(gap_seconds)
    for idx, row in enumerate(segment_rows):
        # For consumer schema, end includes trailing inter-segment delay.
        # Last segment has no trailing delay.
        end_with_gap = int(row["end_ms"]) + (gap_ms if idx < len(segment_rows) - 1 else 0)
        rewritten_items.append(
            {
                "page": int(row["page_number"]),
                "image": row.get("image", ""),
                "subtitle": row["speech"],
                "start": int(row["start_ms"]),
                "end": end_with_gap,
            }
        )

    rewritten_manifest = {
        "version": data.get("version", "1.0"),
        "source_ppt": data.get("source_ppt", ""),
        "source_speech": data.get("source_speech", ""),
        "audio": {
            "voice": args.voice,
            "rate": float(args.rate),
            "model": args.model or "",
            "gap_seconds_between_segments": gap_seconds,
            "full_audio": str(final_audio),
            "total_duration_seconds": round(cursor, 3),
            "timeline_file": str(timeline_path),
        },
        "items": rewritten_items,
    }
    manifest_path.write_text(json.dumps(rewritten_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    preview_html_path = build_preview_html(rewritten_manifest, manifest_path)
    timeline["preview_html"] = str(preview_html_path)
    return timeline


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Synthesize per-page speech and merge into one MP3 with timestamps.")
    p.add_argument("--manifest", required=True, help="path to imagesgallery.json")
    p.add_argument("--out-dir", default="", help="output directory for segment and merged audios")
    p.add_argument("--voice", default="longxiaochun_v3", help="TTS voice id for bl speech synthesize")
    p.add_argument("--rate", type=float, default=1.1, help="speech rate for bl speech synthesize, e.g. 1.1")
    p.add_argument("--model", default="", help="optional TTS model id")
    p.add_argument("--language", default="", help="optional language hint, e.g. zh")
    p.add_argument("--gap-seconds", type=float, default=1.0, help="silence gap inserted between segments")
    p.add_argument("--final-name", default="full_speech.mp3", help="merged audio filename")
    p.add_argument("--timeline-name", default="speech_timestamps.json", help="timeline json filename")
    p.add_argument("--skip-existing", action="store_true", help="reuse existing segment mp3 files when present")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parse_args(argv)
        timeline = build_audio(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        "OK: synthesized "
        f"{len(timeline['items'])} segments -> {timeline['final_audio']}, "
        f"timeline -> {Path(args.out_dir).expanduser().resolve() / args.timeline_name if args.out_dir else Path(timeline['final_audio']).parent / args.timeline_name}, "
        f"preview -> {timeline.get('preview_html', '')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
