#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from align_manuscript import normalize_for_alignment


VERSION = "1.0"


def resolve_bin(name: str) -> str:
    """Resolve executable from PATH or Codex runtime fallback."""
    found = shutil.which(name)
    if found:
        return found

    fallback = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/bin" / name
    if fallback.exists():
        return str(fallback)

    raise FileNotFoundError(f"required executable not found: {name}")


def run_cmd(cmd: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        text=True,
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


def _sorted_page_images(paths: Iterable[Path]) -> List[Path]:
    pattern = re.compile(r"-(\d+)\.png$", re.IGNORECASE)

    def key(path: Path) -> int:
        m = pattern.search(path.name)
        if not m:
            return 10**9
        return int(m.group(1))

    return sorted(paths, key=key)


def convert_ppt_to_images(ppt_path: Path, images_dir: Path, soffice_bin: str, pdftoppm_bin: str) -> List[Path]:
    """Convert PPT/PDF to sequential PNG files under images_dir."""
    suffix = ppt_path.suffix.lower()
    if suffix not in {".ppt", ".pptx", ".pdf"}:
        raise ValueError(f"unsupported input type: {ppt_path.suffix}; expected .ppt/.pptx/.pdf")

    images_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ppt-to-imagesgallery-") as tmp:
        tmp_dir = Path(tmp)
        if suffix == ".pdf":
            pdf_path = ppt_path
        else:
            run_cmd([
                soffice_bin,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(tmp_dir),
                str(ppt_path),
            ])
            expected_pdf = tmp_dir / f"{ppt_path.stem}.pdf"
            if expected_pdf.exists():
                pdf_path = expected_pdf
            else:
                candidates = sorted(tmp_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
                if not candidates:
                    raise RuntimeError("soffice conversion succeeded but no PDF output was found")
                pdf_path = candidates[0]

        ppm_prefix = tmp_dir / "slide"
        run_cmd([pdftoppm_bin, "-png", str(pdf_path), str(ppm_prefix)])

        raw_images = _sorted_page_images(tmp_dir.glob("slide-*.png"))
        if not raw_images:
            raise RuntimeError("pdftoppm produced no PNG files")

        output_images: List[Path] = []
        for idx, raw in enumerate(raw_images, start=1):
            out_name = f"page-{idx:03d}.png"
            out_path = images_dir / out_name
            shutil.copy2(raw, out_path)
            output_images.append(out_path)

        return output_images


def build_imagesgallery(args: argparse.Namespace) -> Dict[str, object]:
    ppt_path = Path(args.ppt).expanduser().resolve()
    speech_path = Path(args.speech).expanduser().resolve()
    out_base = Path(args.out).expanduser().resolve()
    gallery_dir = out_base / "imagesgallery"
    images_dir = gallery_dir / "images"

    if not ppt_path.exists():
        raise FileNotFoundError(f"ppt file not found: {ppt_path}")
    if not speech_path.exists():
        raise FileNotFoundError(f"speech file not found: {speech_path}")

    if gallery_dir.exists():
        shutil.rmtree(gallery_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    soffice_bin = resolve_bin("soffice")
    pdftoppm_bin = resolve_bin("pdftoppm")
    image_abs_paths = convert_ppt_to_images(ppt_path, images_dir, soffice_bin=soffice_bin, pdftoppm_bin=pdftoppm_bin)

    manuscript_raw = speech_path.read_text(encoding="utf-8")
    manuscript_norm = normalize_for_alignment(manuscript_raw)
    if not manuscript_norm:
        raise ValueError("speech content is empty after normalization")

    items: List[Dict[str, object]] = []

    for page_number, image_abs in enumerate(image_abs_paths, start=1):
        rel_image = image_abs.relative_to(gallery_dir).as_posix()
        items.append(
            {
                "page_number": page_number,
                "image": rel_image,
                "speech": f"[DRY_RUN] page {page_number}",
            }
        )

    manifest = {
        "version": VERSION,
        "source_ppt": str(ppt_path),
        "source_speech": str(speech_path),
        "items": items,
    }

    manifest_path = gallery_dir / "imagesgallery.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build imagesgallery skeleton from PPT + manuscript")
    parser.add_argument("--ppt", required=True, help="input .ppt/.pptx/.pdf file")
    parser.add_argument("--speech", required=True, help="input manuscript .txt/.md file")
    parser.add_argument("--out", required=True, help="output base directory")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="accepted for compatibility; script always generates images + skeleton manifest only",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parse_args(argv)
        manifest = build_imagesgallery(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"OK: built imagesgallery with {len(manifest['items'])} items -> "
        f"{Path(args.out).expanduser().resolve() / 'imagesgallery' / 'imagesgallery.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
