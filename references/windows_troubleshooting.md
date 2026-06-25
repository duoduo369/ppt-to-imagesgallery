# Windows Troubleshooting (Ops FAQ)

This page documents high-frequency Windows issues observed in real runs and the recommended fixes.

## 1) `required executable not found: soffice`

### Symptom

Running `build_imagesgallery.py` fails before rendering images:

```text
ERROR: required executable not found: soffice
```

### Why it happens

- LibreOffice is not installed, or
- `soffice` is not in `PATH`.

### Fix options

1. Recommended now (built-in fallback):  
   On Windows, when input is `.ppt/.pptx` and `soffice` is missing, script will auto-fallback to **PowerPoint COM export** (requires local Microsoft PowerPoint).
2. Standard cross-platform setup:
   - Install LibreOffice.
   - Ensure `soffice` is in system `PATH`.

### Quick check

```powershell
where.exe soffice
```

If no result and this is Windows with PowerPoint installed, you can continue using fallback mode.

## 2) Audio synth fails on Windows code page decode

### Symptom

During synthesis step, subprocess reader crashes with GBK decode error:

```text
UnicodeDecodeError: 'gbk' codec can't decode byte ...
```

### Why it happens

Windows default terminal code page may not match tool output encoding.

### Fix

- Script now forces subprocess text decoding to UTF-8 with replacement:
  - `encoding=\"utf-8\"`
  - `errors=\"replace\"`

No manual action required after update.

## 3) Some pages fail when using `bl speech synthesize --text`

### Symptom

Certain pages do not generate `page-XXX.mp3`, then `ffprobe` reports file not found.

### Why it happens

For long or complex text on Windows, direct CLI argument (`--text`) can be unstable in some shells.

### Fix

- Script now always writes per-page text to file and calls:

```bash
bl speech synthesize --text-file <page.txt> ...
```

This is the default path after update.

## 4) Recommended preflight on Windows

Before production runs:

```powershell
bl --version
bl auth status
where.exe ffmpeg
where.exe ffprobe
```

Optional:

```powershell
where.exe soffice
```

If `soffice` is missing but PowerPoint is installed, build step can still proceed via COM fallback.
