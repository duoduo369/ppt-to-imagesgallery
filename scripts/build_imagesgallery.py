#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
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


def _sorted_page_images(paths: Iterable[Path]) -> List[Path]:
    pattern = re.compile(r"-(\d+)\.png$", re.IGNORECASE)

    def key(path: Path) -> int:
        m = pattern.search(path.name)
        if not m:
            return 10**9
        return int(m.group(1))

    return sorted(paths, key=key)


def _export_ppt_via_powerpoint_com(ppt_path: Path, images_dir: Path) -> List[Path]:
    """Windows fallback: export slides directly via PowerPoint COM when soffice is unavailable."""
    if not sys.platform.startswith("win"):
        raise RuntimeError("PowerPoint COM export is only supported on Windows")

    ppt_escaped = str(ppt_path).replace("'", "''")
    out_escaped = str(images_dir).replace("'", "''")
    ps_script = f"""
$ErrorActionPreference='Stop'
$ppt='{ppt_escaped}'
$out='{out_escaped}'
$app = New-Object -ComObject PowerPoint.Application
$pres = $app.Presentations.Open($ppt, $false, $false, $false)
try {{
  $count = $pres.Slides.Count
  for ($i = 1; $i -le $count; $i++) {{
    $dest = Join-Path $out ("page-{{0:D3}}.png" -f $i)
    $pres.Slides.Item($i).Export($dest, "PNG")
  }}
  Write-Output $count
}} finally {{
  $pres.Close()
  $app.Quit()
}}
"""
    run_cmd(["powershell", "-NoProfile", "-Command", ps_script])
    images = _sorted_page_images(images_dir.glob("page-*.png"))
    if not images:
        raise RuntimeError("PowerPoint COM export produced no PNG files")
    return images


def convert_ppt_to_images(
    ppt_path: Path,
    images_dir: Path,
    soffice_bin: str | None,
    pdftoppm_bin: str | None,
) -> List[Path]:
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
            if not soffice_bin:
                # Fallback for Windows ops machines without LibreOffice.
                return _export_ppt_via_powerpoint_com(ppt_path, images_dir)
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

        if not pdftoppm_bin:
            raise FileNotFoundError("required executable not found: pdftoppm")
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


def _read_docx_manuscript(path: Path) -> str:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    wns = ns["w"]

    def _w(tag: str) -> str:
        return f"{{{wns}}}{tag}"

    def _escape_md_cell(text: str) -> str:
        t = (text or "").strip()
        t = t.replace("|", r"\|")
        t = t.replace("\n", "<br>")
        return t

    def _parse_run_text(run: ET.Element) -> str:
        chunks: List[str] = []
        for node in run:
            if node.tag == _w("t"):
                chunks.append(node.text or "")
            elif node.tag == _w("tab"):
                chunks.append(" ")
            elif node.tag in {_w("br"), _w("cr")}:
                chunks.append("\n")
        text = "".join(chunks)
        if not text:
            return ""
        # Keep emphasis stable for downstream slicing/TTS:
        # prefer bold marker; avoid nested/stacked stars that produce *** / **** noise.
        if run.find("./w:rPr/w:b", ns) is not None:
            text = f"**{text}**"
        return text

    def _parse_paragraph(para: ET.Element) -> str:
        texts: List[str] = []
        is_list = para.find(".//w:numPr", ns) is not None

        pstyle = para.find("./w:pPr/w:pStyle", ns)
        heading_level = 0
        if pstyle is not None:
            style_val = pstyle.attrib.get(f"{{{wns}}}val", "")
            m = re.match(r"Heading([1-6])$", style_val, re.IGNORECASE)
            if m:
                heading_level = int(m.group(1))

        for run in para.findall("./w:r", ns):
            run_text = _parse_run_text(run)
            if run_text:
                texts.append(run_text)

        para_text = "".join(texts).strip()
        if not para_text:
            return ""
        if heading_level:
            return f"{'#' * heading_level} {para_text}"
        if is_list:
            return f"- {para_text}"
        return para_text

    def _parse_table(tbl: ET.Element) -> List[str]:
        rows: List[List[str]] = []
        max_cols = 0
        for tr in tbl.findall("./w:tr", ns):
            row_cells: List[str] = []
            for tc in tr.findall("./w:tc", ns):
                cell_paras: List[str] = []
                for p in tc.findall("./w:p", ns):
                    txt = _parse_paragraph(p)
                    if txt:
                        cell_paras.append(txt)
                row_cells.append("\n".join(cell_paras).strip())
            if row_cells:
                max_cols = max(max_cols, len(row_cells))
                rows.append(row_cells)

        if not rows or max_cols == 0:
            return []

        normalized_rows: List[List[str]] = []
        for row in rows:
            normalized_rows.append(row + [""] * (max_cols - len(row)))

        header = normalized_rows[0]
        sep = ["---"] * max_cols
        md_lines = [
            "| " + " | ".join(_escape_md_cell(c) for c in header) + " |",
            "| " + " | ".join(sep) + " |",
        ]
        for row in normalized_rows[1:]:
            md_lines.append("| " + " | ".join(_escape_md_cell(c) for c in row) + " |")
        return md_lines

    with zipfile.ZipFile(path) as zf:
        with zf.open("word/document.xml") as f:
            root = ET.parse(f).getroot()

    lines: List[str] = []
    body = root.find(".//w:body", ns)
    if body is None:
        return ""

    for child in list(body):
        if child.tag == _w("p"):
            para_text = _parse_paragraph(child)
            if para_text:
                lines.append(para_text)
            else:
                lines.append("")
        elif child.tag == _w("tbl"):
            table_lines = _parse_table(child)
            if table_lines:
                lines.extend(table_lines)
                lines.append("")

    # Collapse excessive blanks while keeping paragraph boundaries.
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    # Normalize accidental emphasis stacking produced by rich-text copies.
    out = re.sub(r"\*{4,}", "**", out)
    out = re.sub(r"(?<!\*)\*\*\*(?!\*)", "**", out)
    out = re.sub(r"\u00a0", " ", out)
    return out.strip()


def read_manuscript(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".docx":
        return _read_docx_manuscript(path)
    raise ValueError(f"unsupported speech type: {path.suffix}; expected .txt/.md/.docx")


def build_imagesgallery(args: argparse.Namespace) -> Dict[str, object]:
    ppt_path = Path(args.ppt).expanduser().resolve()
    speech_path = Path(args.speech).expanduser().resolve()
    out_base = Path(args.out).expanduser().resolve()
    ppt_dir_name = ppt_path.stem.strip() or "ppt"
    gallery_dir = out_base / ppt_dir_name / "imagesgallery"
    images_dir = gallery_dir / "images"

    if not ppt_path.exists():
        raise FileNotFoundError(f"ppt file not found: {ppt_path}")
    if not speech_path.exists():
        raise FileNotFoundError(f"speech file not found: {speech_path}")

    if gallery_dir.exists():
        shutil.rmtree(gallery_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    try:
        soffice_bin = resolve_bin("soffice")
    except FileNotFoundError:
        if sys.platform.startswith("win") and ppt_path.suffix.lower() in {".ppt", ".pptx"}:
            soffice_bin = None
        else:
            raise
    need_pdftoppm = (ppt_path.suffix.lower() == ".pdf") or (soffice_bin is not None)
    pdftoppm_bin = resolve_bin("pdftoppm") if need_pdftoppm else None
    image_abs_paths = convert_ppt_to_images(ppt_path, images_dir, soffice_bin=soffice_bin, pdftoppm_bin=pdftoppm_bin)

    manuscript_raw = read_manuscript(speech_path)
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
    parser.add_argument("--speech", required=True, help="input manuscript .txt/.md/.docx file")
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
        f"{Path(args.out).expanduser().resolve() / (Path(args.ppt).expanduser().resolve().stem or 'ppt') / 'imagesgallery' / 'imagesgallery.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
