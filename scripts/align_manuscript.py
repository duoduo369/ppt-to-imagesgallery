#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
import html
import re
from typing import Iterable, List, Sequence, Tuple


@dataclass
class AlignmentIssue:
    page_index: int
    issue_type: str
    detail: str


@dataclass
class AlignmentResult:
    cursor: int
    issues: List[AlignmentIssue]


def normalize_for_alignment(text: str) -> str:
    """Normalize manuscript/page text for robust sequential matching."""
    if not text:
        return ""

    t = text.replace("\xa0", " ").replace("\u200b", "")
    t = html.unescape(t)

    # Convert common HTML line-break and block tags into line boundaries.
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</?(p|div|section|article|blockquote|ul|ol|li|h[1-6])\b[^>]*>", "\n", t)

    # Remove remaining HTML tags while preserving inner text.
    t = re.sub(r"(?is)<[^>]+>", "", t)

    # Recover accidentally escaped Markdown control chars.
    t = re.sub(r"\\([#*_\-])", r"\1", t)
    t = re.sub(r"(?m)^\s*\\-\s*", "- ", t)

    # Remove standalone horizontal rules that are often noise in TTS.
    t = re.sub(r"(?m)^\s*(\*{3,}|-{3,})\s*$", "", t)

    # Basic whitespace normalization.
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def is_substantive_gap(text: str) -> bool:
    """Heuristic: detect whether skipped text likely contains meaningful content."""
    s = (text or "").strip()
    if not s:
        return False
    if re.search(r"(?m)^#{2,3}\s*\S", s):
        return True
    cjk = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
    if cjk >= 12:
        return True
    latin = sum(1 for c in s if c.isalpha())
    return latin >= 24


def _subseq_match_end(haystack: str, start: int, needle: str) -> Tuple[int, float]:
    """Find subsequence match end and match ratio from haystack[start:]."""
    if not needle:
        return start, 1.0

    i = start
    j = 0
    last = start
    n = len(needle)
    while i < len(haystack) and j < n:
        if haystack[i] == needle[j]:
            j += 1
            last = i + 1
        i += 1
    return last, j / n


def _find_page_start(ms: str, cursor: int, page: str, head_lens: Sequence[int] = (80, 56, 40, 28)) -> Tuple[int, str]:
    for length in head_lens:
        head = page[: min(length, len(page))]
        if len(head) < 12:
            continue
        pos = ms.find(head, cursor)
        if pos >= 0:
            return pos, f"head:{length}"
    return -1, "none"


def _advance_cursor(ms: str, pos: int, page: str, min_ratio: float = 0.88) -> Tuple[int, float, str]:
    end, ratio = _subseq_match_end(ms, pos, page)
    if ratio >= min_ratio:
        return end, ratio, "subseq"

    for tail_len in (min(120, len(page)), 80, 48, 32):
        tail = page[-tail_len:]
        if len(tail) < 16:
            continue
        idx = ms.find(tail, pos)
        if idx >= 0:
            return idx + len(tail), max(ratio, 0.95), f"tail:{tail_len}"

    idx = ms.find(page, pos)
    if idx >= 0:
        return idx + len(page), 1.0, "full"

    fallback_end = end if end > pos else min(len(ms), pos + len(page))
    return fallback_end, ratio, "fallback"


def scan_pages_in_manuscript(
    manuscript: str,
    pages: Iterable[str],
    start_cursor: int = 0,
    min_match_ratio: float = 0.88,
) -> AlignmentResult:
    """Scan pages in order and move cursor forward through manuscript."""
    ms = normalize_for_alignment(manuscript)
    cursor = max(0, start_cursor)
    issues: List[AlignmentIssue] = []

    normalized_pages = [normalize_for_alignment((p or "").strip()) for p in pages]
    seen = set()

    for idx, page in enumerate(normalized_pages, start=1):
        if not page:
            issues.append(AlignmentIssue(idx, "empty_speech", "page speech is empty"))
            continue

        page_compact = compact(page)
        if len(page_compact) >= 20 and page_compact in seen:
            issues.append(AlignmentIssue(idx, "duplicate_page_speech", "page speech duplicates earlier content"))
        seen.add(page_compact)

        start, start_mode = _find_page_start(ms, cursor, page)
        if start < 0:
            issues.append(AlignmentIssue(idx, "page_not_found", f"cannot locate page from cursor {cursor}"))
            continue

        gap = ms[cursor:start]
        if is_substantive_gap(gap):
            issues.append(AlignmentIssue(idx, "substantive_gap", f"skipped substantive content before page ({start_mode})"))

        end, ratio, advance_mode = _advance_cursor(ms, start, page, min_ratio=min_match_ratio)
        if ratio < min_match_ratio:
            issues.append(
                AlignmentIssue(
                    idx,
                    "low_match_ratio",
                    f"match ratio {ratio:.3f} below threshold {min_match_ratio:.2f} ({advance_mode})",
                )
            )
        cursor = max(cursor, end)

    return AlignmentResult(cursor=cursor, issues=issues)


def strict_consume_pages(manuscript: str, pages: Iterable[str], start_cursor: int = 0) -> AlignmentResult:
    """Consume pages with strict checks; raise on severe continuity errors."""
    result = scan_pages_in_manuscript(manuscript, pages, start_cursor=start_cursor)
    severe_types = {
        "page_not_found",
        "substantive_gap",
        "low_match_ratio",
    }
    severe = [issue for issue in result.issues if issue.issue_type in severe_types]
    if severe:
        preview = "; ".join(f"p{it.page_index}:{it.issue_type}" for it in severe[:5])
        raise ValueError(f"manuscript continuity check failed: {preview}")
    return result
