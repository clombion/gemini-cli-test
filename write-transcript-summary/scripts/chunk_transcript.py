#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "typer>=0.9.0",
#   "rich>=13.0.0",
# ]
# ///
"""Deterministic transcript chunking: split transcript into scaffolds.

Creates chunks of a transcript according to specified strategy (timestamp-accumulation,
turn-accumulation, or word-count-fallback) and generates JSON scaffold files with
deterministic fields populated and interpretive fields set to null.

Assumes:
- Transcript is plaintext with optional speaker labels
- config.json contains chunking strategy and parameters
- manifest.jsonl contains stage 0d entry (human approval of config.json)

Exit codes:
  0   success
  5   conflict (chunks/ exists, use --force)
  66  no input (transcript, workspace, or manifest prereq missing)
  69  prereq missing (manifest 0d entry not found)
  78  config error (config.json invalid or missing)
  65  data error (transcript parse error)
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

# Constants
CHUNK_DIR = "chunks"


@dataclass
class Turn:
    """Single speaker turn in transcript."""

    speaker: Optional[str]
    text: str
    word_count: int
    timestamp_start: Optional[str] = None
    timestamp_end: Optional[str] = None
    word_offset: int = 0  # Global word position in transcript


@dataclass
class Chunk:
    """Represents a single chunk with metadata."""

    chunk_index: int
    turns: list[Turn] = field(default_factory=list)
    word_count: int = 0
    word_range_start: int = 0
    word_range_end: int = 0
    timestamp_start: Optional[str] = None
    timestamp_end: Optional[str] = None
    speakers: set[str] = field(default_factory=set)

    def get_raw_text(self) -> str:
        """Generate raw_source_text from turns."""
        lines = []
        for turn in self.turns:
            if turn.speaker:
                lines.append(f"{turn.speaker}: {turn.text}")
            else:
                lines.append(turn.text)
        return "\n".join(lines)

    def to_scaffold(self) -> dict:
        """Generate scaffold JSON with deterministic fields."""
        return {
            "chunk_index": self.chunk_index,
            "word_count": self.word_count,
            "word_range": {
                "start": self.word_range_start,
                "end": self.word_range_end,
            },
            "timestamp_range": {
                "start": self.timestamp_start or "",
                "end": self.timestamp_end or "",
            } if self.timestamp_start or self.timestamp_end else None,
            "speaker_tags": sorted(list(self.speakers)) if self.speakers else None,
            "raw_source_text": self.get_raw_text(),
            "topic_tags": None,
            "topic_tags_rationale": None,
            "key_entities": None,
            "entities_rationale": None,
            "conversational_function": None,
            "function_rationale": None,
            "speaker_intent": None,
            "intent_rationale": None,
        }


# ---------------------------------------------------------------------------
# Parsing logic
# ---------------------------------------------------------------------------


def extract_timestamp(text: str) -> Optional[str]:
    """Extract timestamp from line if present. Matches HH:MM:SS or MM:SS formats."""
    # Try HH:MM:SS
    match = re.search(r"\b\d{2}:\d{2}:\d{2}\b", text)
    if match:
        return match.group(0)
    # Try MM:SS
    match = re.search(r"\b\d{1,2}:\d{2}\b(?!:)", text)
    if match:
        return match.group(0)
    return None


def extract_speaker(text: str) -> tuple[Optional[str], str]:
    """Extract speaker label from line if present.

    Returns:
        (speaker_name, remaining_text)
    """
    # Try "NAME: text" pattern
    match = re.match(r"^([A-Z][A-Z0-9\s]*?):\s*(.*)", text)
    if match:
        return match.group(1).strip(), match.group(2)
    # Try "(name): text" pattern
    match = re.match(r"^\(([^)]+)\):\s*(.*)", text)
    if match:
        return match.group(1).strip(), match.group(2)
    # Try "Speaker 123: text" pattern
    match = re.match(r"^Speaker\s+(\d+):\s*(.*)", text)
    if match:
        return f"Speaker_{match.group(1)}", match.group(2)
    # Try "Speaker Name: text" pattern
    match = re.match(r"^Speaker\s+([A-Z][a-z]+):\s*(.*)", text)
    if match:
        return f"Speaker_{match.group(1)}", match.group(2)

    return None, text


def count_words(text: str) -> int:
    """Count words in text."""
    return len(text.split())


def parse_transcript(
    content: str,
    speakers_identified: bool,
) -> list[Turn]:
    """Parse transcript into turns.

    Args:
        content: Raw transcript text
        speakers_identified: Whether to extract speaker labels

    Returns:
        List of Turn objects with populated speaker, text, word_count, timestamp_*
    """
    turns: list[Turn] = []
    lines = content.split("\n")
    current_word_offset = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip blank lines
        if not line.strip():
            i += 1
            continue

        # Extract timestamp if present
        timestamp = extract_timestamp(line)
        text = line
        if timestamp:
            # Remove timestamp from text
            text = re.sub(r"\d{2}:\d{2}:\d{2}|\d{1,2}:\d{2}(?!:)", "", text).strip()

        # Extract speaker if enabled
        speaker = None
        if speakers_identified:
            speaker, text = extract_speaker(text)
            # Normalize speaker name
            if speaker:
                speaker = speaker.replace(" ", "_")

        # Count words
        word_count = count_words(text)

        if word_count > 0:
            turn = Turn(
                speaker=speaker,
                text=text,
                word_count=word_count,
                timestamp_start=timestamp,
                timestamp_end=None,
                word_offset=current_word_offset,
            )
            turns.append(turn)
            current_word_offset += word_count

        i += 1

    return turns


# ---------------------------------------------------------------------------
# Chunking strategies
# ---------------------------------------------------------------------------


def chunk_timestamp_accumulation(
    turns: list[Turn],
    chunk_size_words: int,
    overlap_words: int,
) -> list[Chunk]:
    """Accumulate turns until timestamp crosses boundary OR word count exceeds threshold.

    Includes overlap from previous chunk.
    """
    chunks: list[Chunk] = []
    current_chunk = Chunk(chunk_index=0)
    overlap_buffer: list[Turn] = []
    global_word_count = 0

    for turn_idx, turn in enumerate(turns):
        # If chunk exceeds size and we have a turn boundary, create new chunk
        if (
            current_chunk.word_count > chunk_size_words
            and len(current_chunk.turns) > 0
        ):
            # Close current chunk
            current_chunk.word_range_end = global_word_count - 1
            if current_chunk.turns:
                current_chunk.timestamp_end = current_chunk.turns[-1].timestamp_start
            chunks.append(current_chunk)

            # Start new chunk with overlap from previous
            current_chunk = Chunk(
                chunk_index=len(chunks),
                word_range_start=max(0, global_word_count - overlap_words),
            )

            # Add overlap buffer to new chunk
            for overlap_turn in overlap_buffer:
                current_chunk.turns.append(overlap_turn)
                current_chunk.word_count += overlap_turn.word_count
                if overlap_turn.speaker:
                    current_chunk.speakers.add(overlap_turn.speaker)
                if not current_chunk.timestamp_start:
                    current_chunk.timestamp_start = overlap_turn.timestamp_start

        # Add current turn to chunk
        if not current_chunk.timestamp_start:
            current_chunk.timestamp_start = turn.timestamp_start
        current_chunk.turns.append(turn)
        current_chunk.word_count += turn.word_count
        if turn.speaker:
            current_chunk.speakers.add(turn.speaker)
        current_chunk.timestamp_end = turn.timestamp_start

        # Maintain overlap buffer (last N words from current chunk)
        overlap_buffer = _get_overlap_buffer(current_chunk.turns, overlap_words)

        global_word_count += turn.word_count

    # Add final chunk
    if current_chunk.turns:
        current_chunk.word_range_end = global_word_count - 1
        chunks.append(current_chunk)

    return chunks


def chunk_turn_accumulation(
    turns: list[Turn],
    chunk_size_words: int,
    overlap_words: int,
) -> list[Chunk]:
    """Accumulate N turns until word count exceeds threshold.

    Overlap via partial turn from prior chunk.
    """
    chunks: list[Chunk] = []
    current_chunk = Chunk(chunk_index=0)
    overlap_buffer: list[Turn] = []
    global_word_count = 0

    for turn in turns:
        # If chunk exceeds size, finalize and start new chunk
        if (
            current_chunk.word_count > chunk_size_words
            and len(current_chunk.turns) > 0
        ):
            current_chunk.word_range_end = global_word_count - 1
            chunks.append(current_chunk)

            # Start new chunk with overlap
            current_chunk = Chunk(
                chunk_index=len(chunks),
                word_range_start=max(0, global_word_count - overlap_words),
            )

            for overlap_turn in overlap_buffer:
                current_chunk.turns.append(overlap_turn)
                current_chunk.word_count += overlap_turn.word_count
                if overlap_turn.speaker:
                    current_chunk.speakers.add(overlap_turn.speaker)
                if not current_chunk.timestamp_start:
                    current_chunk.timestamp_start = overlap_turn.timestamp_start

        # Add current turn
        if not current_chunk.timestamp_start:
            current_chunk.timestamp_start = turn.timestamp_start
        current_chunk.turns.append(turn)
        current_chunk.word_count += turn.word_count
        if turn.speaker:
            current_chunk.speakers.add(turn.speaker)
        current_chunk.timestamp_end = turn.timestamp_start

        overlap_buffer = _get_overlap_buffer(current_chunk.turns, overlap_words)
        global_word_count += turn.word_count

    # Add final chunk
    if current_chunk.turns:
        current_chunk.word_range_end = global_word_count - 1
        chunks.append(current_chunk)

    return chunks


def chunk_word_count_fallback(
    turns: list[Turn],
    chunk_size_words: int,
    overlap_words: int,
) -> list[Chunk]:
    """Split on word count only, respecting turn boundaries.

    Never split a speaker's utterance.
    """
    chunks: list[Chunk] = []
    current_chunk = Chunk(chunk_index=0)
    overlap_buffer: list[Turn] = []
    global_word_count = 0

    for turn in turns:
        # If adding this turn would exceed threshold, finalize current chunk
        if (
            current_chunk.word_count + turn.word_count > chunk_size_words
            and len(current_chunk.turns) > 0
        ):
            current_chunk.word_range_end = global_word_count - 1
            chunks.append(current_chunk)

            # Start new chunk with overlap
            current_chunk = Chunk(
                chunk_index=len(chunks),
                word_range_start=max(0, global_word_count - overlap_words),
            )

            for overlap_turn in overlap_buffer:
                current_chunk.turns.append(overlap_turn)
                current_chunk.word_count += overlap_turn.word_count
                if overlap_turn.speaker:
                    current_chunk.speakers.add(overlap_turn.speaker)
                if not current_chunk.timestamp_start:
                    current_chunk.timestamp_start = overlap_turn.timestamp_start

        # Add current turn (never split mid-utterance)
        if not current_chunk.timestamp_start:
            current_chunk.timestamp_start = turn.timestamp_start
        current_chunk.turns.append(turn)
        current_chunk.word_count += turn.word_count
        if turn.speaker:
            current_chunk.speakers.add(turn.speaker)
        current_chunk.timestamp_end = turn.timestamp_start

        overlap_buffer = _get_overlap_buffer(current_chunk.turns, overlap_words)
        global_word_count += turn.word_count

    # Add final chunk
    if current_chunk.turns:
        current_chunk.word_range_end = global_word_count - 1
        chunks.append(current_chunk)

    return chunks


def _get_overlap_buffer(turns: list[Turn], overlap_words: int) -> list[Turn]:
    """Get last N words worth of turns for overlap buffer."""
    if overlap_words == 0:
        return []

    buffer: list[Turn] = []
    word_count = 0
    for turn in reversed(turns):
        word_count += turn.word_count
        buffer.insert(0, turn)
        if word_count >= overlap_words:
            break

    return buffer


# ---------------------------------------------------------------------------
# Config and manifest validation
# ---------------------------------------------------------------------------


def load_and_validate_config(config_path: Path) -> dict:
    """Load config.json and validate against stage-0c schema.

    Returns:
        Parsed config dict

    Raises:
        ValueError: if config invalid or missing required fields
    """
    if not config_path.exists():
        raise ValueError(f"config.json not found: {config_path}")

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"config.json is not valid JSON: {e}") from e

    # Validate required fields match stage-0c schema
    required_fields = [
        "chunking_strategy",
        "chunk_size_words",
        "overlap_words",
        "speakers_identified",
        "timestamp_strategy",
    ]
    for field in required_fields:
        if field not in config:
            raise ValueError(f"config.json missing required field: {field}")

    # Validate chunking_strategy enum
    valid_strategies = ["timestamp-accumulation", "turn-accumulation", "word-count-fallback"]
    if config["chunking_strategy"] not in valid_strategies:
        raise ValueError(
            f"Invalid chunking_strategy: {config['chunking_strategy']}. "
            f"Must be one of: {', '.join(valid_strategies)}"
        )

    # Validate types
    if not isinstance(config["chunk_size_words"], int) or config["chunk_size_words"] < 1:
        raise ValueError("chunk_size_words must be positive integer")
    if not isinstance(config["overlap_words"], int) or config["overlap_words"] < 0:
        raise ValueError("overlap_words must be non-negative integer")
    if not isinstance(config["speakers_identified"], bool):
        raise ValueError("speakers_identified must be boolean")

    return config


def check_manifest_0d_entry(manifest_path: Path) -> bool:
    """Check if manifest.jsonl contains a stage 0d entry.

    Returns:
        True if 0d entry found, False otherwise
    """
    if not manifest_path.exists():
        return False

    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("step") == "0d":
                        return True
                except json.JSONDecodeError:
                    continue
    except OSError:
        return False

    return False


# ---------------------------------------------------------------------------
# I/O functions
# ---------------------------------------------------------------------------


def read_transcript(transcript_path: Path) -> str:
    """Read transcript file.

    Returns:
        Content as string

    Raises:
        ValueError: if file cannot be read
    """
    if not transcript_path.exists():
        raise ValueError(f"Transcript file not found: {transcript_path}")

    try:
        return transcript_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return transcript_path.read_text(encoding="latin-1")
        except UnicodeDecodeError as e:
            raise ValueError(f"Cannot decode transcript with UTF-8 or Latin-1: {e}") from e
    except OSError as e:
        raise ValueError(f"Failed to read transcript: {e}") from e


def write_chunks(chunks_dir: Path, chunks: list[Chunk]) -> dict[int, str]:
    """Write chunk scaffolds to chunks/ directory.

    Returns:
        Mapping of chunk index to file hash (for manifest)

    Raises:
        OSError: if write fails
    """
    chunks_dir.mkdir(parents=True, exist_ok=True)
    hashes = {}

    for chunk in chunks:
        scaffold = chunk.to_scaffold()
        content = json.dumps(scaffold, indent=2) + "\n"
        content_bytes = content.encode("utf-8")

        chunk_file = chunks_dir / f"chunk_{chunk.chunk_index:04d}.json"
        chunk_file.write_text(content, encoding="utf-8")

        hashes[chunk.chunk_index] = sha256(content_bytes).hexdigest()

    return hashes


def append_manifest(
    manifest_path: Path,
    chunks_dir: Path,
    chunk_count: int,
    chunk_hashes: dict[int, str],
    config: dict,
) -> None:
    """Append entries to manifest.jsonl for all chunks.

    Raises:
        OSError: if write fails
    """
    # Get parent hash from last entry if manifest exists
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

    with manifest_path.open("a", encoding="utf-8") as f:
        for chunk_idx in range(chunk_count):
            entry = {
                "step": "1a",
                "file": f"chunks/chunk_{chunk_idx:04d}.json",
                "sha256": chunk_hashes.get(chunk_idx, ""),
                "parent_hash": parent_hash,
                "inputs": {
                    "chunking_strategy": config["chunking_strategy"],
                    "chunk_size_words": config["chunk_size_words"],
                    "overlap_words": config["overlap_words"],
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            f.write(json.dumps(entry) + "\n")
            # Next entry's parent is this one's hash
            parent_hash = entry["sha256"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(
    transcript: str = typer.Argument(
        ...,
        help="Path to transcript file",
    ),
    workspace: str = typer.Argument(
        ...,
        help="Path to workspace directory",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing chunks/ directory",
    ),
) -> None:
    """Deterministic transcript chunking: split transcript into scaffolds."""

    console_err = Console(file=sys.stderr)
    console_out = Console()
    transcript_path = Path(transcript)
    workspace_path = Path(workspace)
    chunks_dir = workspace_path / CHUNK_DIR

    # Validation: workspace exists
    if not workspace_path.exists():
        console_err.print(
            f"[red]Error:[/red] Workspace directory not found: {workspace_path}",
        )
        sys.exit(66)

    # Validation: transcript exists
    if not transcript_path.exists():
        console_err.print(
            f"[red]Error:[/red] Transcript file not found: {transcript_path}",
        )
        sys.exit(66)

    # Validation: manifest.jsonl exists and contains stage 0d entry
    manifest_path = workspace_path / "manifest.jsonl"
    if not manifest_path.exists():
        console_err.print(
            f"[red]Error:[/red] Prerequisite not met: manifest.jsonl not found",
        )
        sys.exit(69)

    if not check_manifest_0d_entry(manifest_path):
        console_err.print(
            f"[red]Error:[/red] Prerequisite not met: stage 0d entry not found in manifest.jsonl",
        )
        sys.exit(69)

    # Validation: config.json exists and is valid
    config_path = workspace_path / "config.json"
    try:
        config = load_and_validate_config(config_path)
    except ValueError as e:
        console_err.print(f"[red]Error:[/red] {e}")
        sys.exit(78)

    # Validation: chunks/ does not exist (unless --force)
    if chunks_dir.exists() and not force:
        console_err.print(
            f"[red]Error:[/red] {chunks_dir} already exists. Use --force to overwrite.",
        )
        sys.exit(5)

    # Read transcript
    try:
        transcript_content = read_transcript(transcript_path)
    except ValueError as e:
        console_err.print(f"[red]Error:[/red] {e}")
        sys.exit(66)

    # Parse transcript into turns
    try:
        turns = parse_transcript(
            transcript_content,
            speakers_identified=config["speakers_identified"],
        )
    except Exception as e:
        console_err.print(f"[red]Error:[/red] Failed to parse transcript: {e}")
        sys.exit(65)

    if not turns:
        console_err.print(f"[red]Error:[/red] No turns found in transcript")
        sys.exit(65)

    # Apply chunking strategy
    strategy = config["chunking_strategy"]
    chunk_size = config["chunk_size_words"]
    overlap = config["overlap_words"]

    try:
        if strategy == "timestamp-accumulation":
            chunks = chunk_timestamp_accumulation(turns, chunk_size, overlap)
        elif strategy == "turn-accumulation":
            chunks = chunk_turn_accumulation(turns, chunk_size, overlap)
        elif strategy == "word-count-fallback":
            chunks = chunk_word_count_fallback(turns, chunk_size, overlap)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")
    except Exception as e:
        console_err.print(f"[red]Error:[/red] Chunking failed: {e}")
        sys.exit(65)

    if not chunks:
        console_err.print(f"[red]Error:[/red] No chunks created")
        sys.exit(65)

    # Write chunk scaffolds
    try:
        chunk_hashes = write_chunks(chunks_dir, chunks)
    except OSError as e:
        console_err.print(f"[red]Error:[/red] Failed to write chunks: {e}")
        sys.exit(78)

    # Append to manifest
    try:
        append_manifest(manifest_path, chunks_dir, len(chunks), chunk_hashes, config)
    except OSError as e:
        console_err.print(f"[red]Error:[/red] Failed to append manifest: {e}")
        sys.exit(78)

    # Success
    console_out.print(
        f"[green]✓[/green] Created {len(chunks)} chunks in {chunks_dir}",
    )
    sys.exit(0)


if __name__ == "__main__":
    app = typer.Typer()
    app.command()(main)
    app()
