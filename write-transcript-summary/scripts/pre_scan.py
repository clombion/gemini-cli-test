#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "typer>=0.9.0",
#   "rich>=13.0.0",
# ]
# ///
"""Deterministic transcript pre-scan: extract signals without LLM.

Analyzes a raw transcript file and extracts structural signals:
- File format and encoding
- Timestamp presence, format, and coverage
- Speaker label presence and types
- Segment/dialogue turn estimation
- Quality warnings (long lines, encoding issues, etc.)

Assumptions:
- Transcript is plaintext (txt, srt, vtt, or similar)
- Workspace directory exists and is writable
- No special character encoding beyond UTF-8/Latin-1

Exit codes:
  0   success
  5   conflict (file exists, use --force)
  66  no input (transcript or workspace not found)
  65  data error (unreadable encoding)
  78  config error (workspace not writable)
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console

# Constants
DEFAULT_ENCODING = "utf-8"
FALLBACK_ENCODING = "latin-1"
MAX_REASONABLE_LINE_LENGTH = 1000

# Timestamp patterns (order matters: most specific first)
TIMESTAMP_PATTERNS = {
    "hh:mm:ss": re.compile(r"\b\d{2}:\d{2}:\d{2}\b"),
    "mm:ss": re.compile(r"\b\d{1,2}:\d{2}\b(?!:)"),
    "[hh:mm:ss]": re.compile(r"\[\d{2}:\d{2}:\d{2}\]"),
    "[mm:ss]": re.compile(r"\[\d{1,2}:\d{2}\]"),
}

# Speaker patterns (order matters: most specific first)
# Note: patterns must work with or without preceding timestamps/brackets
SPEAKER_PATTERNS = [
    re.compile(r"(?:^|\]|\s)([A-Z][A-Z0-9\s]*?):\s*", re.MULTILINE),  # NAME: (may follow timestamp)
    re.compile(r"(?:^|\]|\s)\(([^)]+)\):\s*", re.MULTILINE),  # (name):
    re.compile(r"(?:^|\]|\s)Speaker\s+(\d+):", re.MULTILINE),  # Speaker 123:
    re.compile(r"(?:^|\]|\s)Speaker\s+[A-Z][a-z]*:", re.MULTILINE),  # Speaker Name:
]


# ---------------------------------------------------------------------------
# Pure business logic (no I/O)
# ---------------------------------------------------------------------------


def detect_file_format(transcript_path: Path) -> str:
    """Detect file format from extension."""
    suffix = transcript_path.suffix.lower().lstrip(".")
    if not suffix:
        return "txt"
    return suffix


def detect_encoding(content_bytes: bytes) -> tuple[str, bool]:
    """Detect encoding: try UTF-8 first, then fall back to Latin-1.

    Returns:
        (encoding_name, had_errors)
    """
    try:
        content_bytes.decode(DEFAULT_ENCODING)
        return DEFAULT_ENCODING, False
    except UnicodeDecodeError:
        try:
            content_bytes.decode(FALLBACK_ENCODING)
            return FALLBACK_ENCODING, True
        except UnicodeDecodeError:
            # Last resort: decode with replacement
            return FALLBACK_ENCODING, True


def detect_timestamps(
    content: str,
) -> tuple[bool, Optional[str], int, str, float]:
    """Detect timestamps and their characteristics.

    Returns:
        (present, format, count, regularity, coverage)
    """
    if not content:
        return False, None, 0, "none", 0.0

    lines = content.split("\n")
    timestamp_by_format: dict[str, list[int]] = {fmt: [] for fmt in TIMESTAMP_PATTERNS}

    for line_idx, line in enumerate(lines):
        for fmt, pattern in TIMESTAMP_PATTERNS.items():
            if pattern.search(line):
                timestamp_by_format[fmt].append(line_idx)

    # Find the format with the most matches
    best_format = max(
        timestamp_by_format.items(),
        key=lambda x: len(x[1]),
        default=(None, []),
    )

    if not best_format[1]:
        return False, None, 0, "none", 0.0

    fmt_name, line_indices = best_format
    count = len(line_indices)

    # Estimate regularity
    if count == 0:
        regularity = "none"
    elif count == len(lines):
        regularity = "per-utterance"
    elif count > len(lines) * 0.9:
        regularity = "per-utterance"
    elif count > len(lines) * 0.5:
        regularity = "frequent"
    elif count > len(lines) * 0.1:
        regularity = "sparse"
    else:
        regularity = "very-sparse"

    coverage = count / len(lines) if lines else 0.0

    return True, fmt_name, count, regularity, coverage


def detect_speakers(content: str) -> tuple[bool, int, str]:
    """Detect speaker labels and their characteristics.

    Returns:
        (present, distinct_count, type)
    """
    if not content:
        return False, 0, "none"

    speakers_found: dict[str, int] = {}

    for pattern in SPEAKER_PATTERNS:
        matches = pattern.findall(content)
        for match in matches:
            speakers_found[match] = speakers_found.get(match, 0) + 1

    if not speakers_found:
        return False, 0, "none"

    # Determine speaker type: named vs numbered
    speaker_names = list(speakers_found.keys())
    is_numbered = all(name.isdigit() for name in speaker_names)
    speaker_type = "numbered" if is_numbered else "named"

    return True, len(speakers_found), speaker_type


def estimate_segments(content: str, speaker_present: bool) -> int:
    """Estimate number of dialogue turns/segments.

    Strategy: count speaker changes if speakers present, else count blank lines.
    """
    if not content:
        return 0

    if speaker_present:
        # Count speaker label occurrences as a proxy for segments
        count = 0
        for pattern in SPEAKER_PATTERNS:
            count = max(count, len(pattern.findall(content)))
        return count if count > 0 else 1

    # Fallback: count non-empty paragraph groups
    lines = content.split("\n")
    segments = 0
    in_paragraph = False
    for line in lines:
        if line.strip():
            if not in_paragraph:
                segments += 1
                in_paragraph = True
        else:
            in_paragraph = False
    return max(segments, 1)


def check_quality(content: str, lines: list[str]) -> list[str]:
    """Check for quality issues."""
    warnings = []

    # Check for very long lines
    max_line_len = max((len(line) for line in lines), default=0)
    if max_line_len > MAX_REASONABLE_LINE_LENGTH:
        warnings.append(f"Long lines detected (max {max_line_len} chars)")

    # Check for mixed line endings
    crlf_count = content.count("\r\n")
    lf_count = content.count("\n") - crlf_count
    cr_count = content.count("\r") - crlf_count
    line_ending_types = sum([crlf_count > 0, lf_count > 0, cr_count > 0])
    if line_ending_types > 1:
        warnings.append("Mixed line endings detected (CRLF and LF)")

    # Check for suspicious Unicode
    try:
        content.encode("ascii")
    except UnicodeEncodeError:
        # This is expected for real transcripts, but note it if there are control chars
        if any(ord(c) < 32 and c not in "\n\r\t" for c in content):
            warnings.append("Control characters detected")

    return warnings


def compute_sha256(data: bytes) -> str:
    """Compute SHA256 hash of data."""
    return sha256(data).hexdigest()


def build_signal_dict(
    transcript_path: Path,
    content: str,
    content_bytes: bytes,
    encoding: str,
) -> dict[str, Any]:
    """Build the signals dictionary."""
    lines = content.split("\n")
    char_count = len(content)
    line_count = len(lines)

    timestamp_present, timestamp_format, timestamp_count, regularity, coverage = (
        detect_timestamps(content)
    )
    speaker_present, distinct_speakers, speaker_type = detect_speakers(content)
    segment_count = estimate_segments(content, speaker_present)
    warnings = check_quality(content, lines)

    signals: dict[str, Any] = {
        "file_format": detect_file_format(transcript_path),
        "encoding": encoding,
        "character_count": char_count,
        "line_count": line_count,
        "timestamp_present": timestamp_present,
    }

    if timestamp_present:
        signals["timestamp_format"] = timestamp_format
        signals["timestamp_regularity"] = regularity
        signals["estimated_timestamp_coverage"] = round(coverage, 2)

    signals["speaker_label_present"] = speaker_present
    if speaker_present:
        signals["distinct_speakers"] = distinct_speakers
        signals["speaker_type"] = speaker_type

    signals["estimated_segment_count"] = segment_count

    if warnings:
        signals["warnings"] = warnings

    return signals


# ---------------------------------------------------------------------------
# I/O functions
# ---------------------------------------------------------------------------


def read_transcript(transcript_path: Path) -> tuple[str, bytes, str]:
    """Read transcript file and detect encoding.

    Returns:
        (content_as_str, content_as_bytes, encoding_used)

    Raises:
        ValueError: if file cannot be read
    """
    try:
        content_bytes = transcript_path.read_bytes()
    except FileNotFoundError as e:
        raise ValueError(f"Transcript file not found: {transcript_path}") from e
    except OSError as e:
        raise ValueError(f"Failed to read transcript: {e}") from e

    encoding, had_errors = detect_encoding(content_bytes)
    try:
        content = content_bytes.decode(encoding)
    except UnicodeDecodeError as e:
        raise ValueError(f"Failed to decode file with {encoding}: {e}") from e

    return content, content_bytes, encoding


def write_signals(workspace: Path, signals: dict[str, Any]) -> bytes:
    """Write signals to raw_signals.json.

    Returns:
        content_bytes (for manifest hash)

    Raises:
        OSError: if write fails
    """
    output_path = workspace / "raw_signals.json"
    content = json.dumps(signals, indent=2) + "\n"
    content_bytes = content.encode("utf-8")
    output_path.write_text(content, encoding="utf-8")
    return content_bytes


def append_manifest(
    workspace: Path,
    signals_hash: str,
    transcript_path: Path,
) -> None:
    """Append entry to manifest.jsonl.

    Raises:
        OSError: if write fails
    """
    manifest_path = workspace / "manifest.jsonl"

    # Compute parent hash if manifest exists and has content
    parent_hash = None
    if manifest_path.exists():
        try:
            with manifest_path.open("r", encoding="utf-8") as f:
                lines = f.readlines()
                if lines:
                    last_line = lines[-1].strip()
                    if last_line:
                        last_entry = json.loads(last_line)
                        parent_hash = last_entry.get("sha256")
        except (json.JSONDecodeError, OSError):
            parent_hash = None

    entry = {
        "step": "0a",
        "file": "raw_signals.json",
        "sha256": signals_hash,
        "parent_hash": parent_hash,
        "inputs": {"transcript": str(transcript_path)},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(
    transcript: str = typer.Argument(
        ...,
        help="Path to transcript file (plaintext, srt, vtt, etc.)",
    ),
    workspace: str = typer.Argument(
        ...,
        help="Path to workspace directory",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing raw_signals.json",
    ),
) -> None:
    """Deterministic transcript pre-scan: extract signals without LLM."""

    console_err = Console(file=sys.stderr)
    console_out = Console()
    transcript_path = Path(transcript)
    workspace_path = Path(workspace)

    # Validation: transcript exists
    if not transcript_path.exists():
        console_err.print(
            f"[red]Error:[/red] Transcript file not found: {transcript_path}",
        )
        sys.exit(66)

    # Validation: workspace exists
    if not workspace_path.exists():
        console_err.print(
            f"[red]Error:[/red] Workspace directory not found: {workspace_path}",
        )
        sys.exit(66)

    # Validation: workspace is a directory
    if not workspace_path.is_dir():
        console_err.print(
            f"[red]Error:[/red] Workspace is not a directory: {workspace_path}",
        )
        sys.exit(66)

    # Validation: raw_signals.json does not exist (unless --force)
    signals_path = workspace_path / "raw_signals.json"
    if signals_path.exists() and not force:
        console_err.print(
            f"[red]Error:[/red] {signals_path} already exists. Use --force to overwrite.",
        )
        sys.exit(5)

    # Validation: workspace is writable (test by trying to write manifest)
    manifest_path = workspace_path / "manifest.jsonl"
    try:
        # Test write by opening in append mode (creates if not exists)
        with manifest_path.open("a", encoding="utf-8"):
            pass
    except (OSError, PermissionError) as e:
        console_err.print(
            f"[red]Error:[/red] Workspace is not writable: {e}",
        )
        sys.exit(78)

    # Read and analyze transcript
    try:
        content, content_bytes, encoding = read_transcript(transcript_path)
    except ValueError as e:
        console_err.print(
            f"[red]Error:[/red] {e}",
        )
        sys.exit(65)

    # Build signals
    signals = build_signal_dict(transcript_path, content, content_bytes, encoding)

    # Write signals
    try:
        signals_bytes = write_signals(workspace_path, signals)
    except OSError as e:
        console_err.print(
            f"[red]Error:[/red] Failed to write signals: {e}",
        )
        sys.exit(78)

    # Append to manifest
    signals_hash = compute_sha256(signals_bytes)
    try:
        append_manifest(workspace_path, signals_hash, transcript_path)
    except OSError as e:
        console_err.print(
            f"[red]Error:[/red] Failed to append manifest: {e}",
        )
        sys.exit(78)

    # Success
    console_out.print(
        f"[green]✓[/green] Signals extracted: {signals_path}",
    )
    sys.exit(0)


if __name__ == "__main__":
    app = typer.Typer()
    app.command()(main)
    app()
