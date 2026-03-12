#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "typer>=0.9.0",
#   "rich>=13.0.0",
# ]
# ///
"""Pipeline status reporter: dual-use for humans and LLM.

Reads manifest.jsonl and artifact directories to determine pipeline state.
Reports per-stage status, next action, and blocked gates.

Exit codes:
  0   success
  66  workspace not found
  78  manifest not found
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

# Constants
STAGES = [
    ("0a", "Raw pre-scan"),
    ("0b", "Analysis"),
    ("0c", "Config synthesis"),
    ("0d", "Human gate"),
    ("1a", "Chunking"),
    ("1c", "Label validation"),
    ("2a", "Topic map"),
    ("2c", "Human gate"),
    ("3", "Extraction"),
    ("4", "Summary output"),
]

HUMAN_GATES = {"0d", "2c", "4g"}

STATUS_SYMBOLS = {
    "done": "✓",
    "pending": "○",
    "blocked_on_gate": "⧗",
    "failed": "✗",
}


@dataclass
class StageStatus:
    """Status of a single pipeline stage."""

    id: str
    name: str
    status: str  # done, pending, blocked_on_gate, failed
    gate_artifact: Optional[str] = None
    error: Optional[str] = None


@dataclass
class NextAction:
    """Next action to advance pipeline."""

    stage: str
    action: str  # action type
    artifact: Optional[str] = None
    path: Optional[str] = None


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


def load_manifest(workspace: Path) -> list[dict]:
    """Load and parse manifest.jsonl.

    Returns:
        List of manifest entries, in order.

    Raises:
        FileNotFoundError: if manifest.jsonl not found
        ValueError: if manifest has invalid JSON
    """
    manifest_path = workspace / "manifest.jsonl"

    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.jsonl not found: {manifest_path}")

    entries = []
    with manifest_path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Invalid JSON in manifest at line {line_num}: {e}"
                ) from e

    return entries


# ---------------------------------------------------------------------------
# Stage status detection
# ---------------------------------------------------------------------------


def check_stage_0a(workspace: Path, manifest: list[dict]) -> StageStatus:
    """Stage 0a: Raw pre-scan (raw_signals.json exists)."""
    signals_path = workspace / "raw_signals.json"

    if signals_path.exists():
        return StageStatus("0a", "Raw pre-scan", "done")
    return StageStatus("0a", "Raw pre-scan", "pending")


def check_stage_0b(workspace: Path, manifest: list[dict]) -> StageStatus:
    """Stage 0b: Analysis (from manifest entry)."""
    for entry in manifest:
        if entry.get("step") == "0b":
            return StageStatus("0b", "Analysis", "done")
    return StageStatus("0b", "Analysis", "pending")


def check_stage_0c(workspace: Path, manifest: list[dict]) -> StageStatus:
    """Stage 0c: Config synthesis (from manifest entry)."""
    for entry in manifest:
        if entry.get("step") == "0c":
            return StageStatus("0c", "Config synthesis", "done")
    return StageStatus("0c", "Config synthesis", "pending")


def check_stage_0d(workspace: Path, manifest: list[dict]) -> StageStatus:
    """Stage 0d: Human gate (config.json approval in manifest)."""
    config_path = workspace / "config.json"

    # Check if config.json is approved (marked in manifest)
    for entry in manifest:
        if entry.get("step") == "0d" and entry.get("file") == "config.json":
            # Found approval entry
            return StageStatus("0d", "Human gate", "done")

    # config.json must exist to be waiting for approval
    if config_path.exists():
        return StageStatus(
            "0d",
            "Human gate",
            "blocked_on_gate",
            gate_artifact="config.json",
        )

    # config.json doesn't exist yet
    return StageStatus("0d", "Human gate", "pending")


def check_stage_1a(workspace: Path, manifest: list[dict]) -> StageStatus:
    """Stage 1a: Chunking (chunks/*.json exist)."""
    chunks_dir = workspace / "chunks"

    if chunks_dir.exists() and list(chunks_dir.glob("*.json")):
        return StageStatus("1a", "Chunking", "done")
    return StageStatus("1a", "Chunking", "pending")


def check_stage_1c(workspace: Path, manifest: list[dict]) -> StageStatus:
    """Stage 1c: Label validation (from manifest entry)."""
    for entry in manifest:
        if entry.get("step") == "1c":
            return StageStatus("1c", "Label validation", "done")
    return StageStatus("1c", "Label validation", "pending")


def check_stage_2a(workspace: Path, manifest: list[dict]) -> StageStatus:
    """Stage 2a: Topic map (topics/topic_map.json exists)."""
    topic_map_path = workspace / "topics" / "topic_map.json"

    if topic_map_path.exists():
        return StageStatus("2a", "Topic map", "done")
    return StageStatus("2a", "Topic map", "pending")


def check_stage_2c(workspace: Path, manifest: list[dict]) -> StageStatus:
    """Stage 2c: Human gate (topic map approval in manifest)."""
    topic_map_path = workspace / "topics" / "topic_map.json"

    # Check if topic_map.json is approved (marked in manifest)
    for entry in manifest:
        if entry.get("step") == "2c" and entry.get("file") == "topics/topic_map.json":
            return StageStatus("2c", "Human gate", "done")

    # topic_map.json must exist to be waiting for approval
    if topic_map_path.exists():
        return StageStatus(
            "2c",
            "Human gate",
            "blocked_on_gate",
            gate_artifact="topics/topic_map.json",
        )

    return StageStatus("2c", "Human gate", "pending")


def check_stage_3(workspace: Path, manifest: list[dict]) -> StageStatus:
    """Stage 3: Extraction (extracts/*.json exist)."""
    extracts_dir = workspace / "extracts"

    if extracts_dir.exists() and list(extracts_dir.glob("*.json")):
        return StageStatus("3", "Extraction", "done")
    return StageStatus("3", "Extraction", "pending")


def check_stage_4(workspace: Path, manifest: list[dict]) -> StageStatus:
    """Stage 4: Summary output (output/summary.md exists)."""
    summary_path = workspace / "output" / "summary.md"

    if summary_path.exists():
        return StageStatus("4", "Summary output", "done")
    return StageStatus("4", "Summary output", "pending")


def detect_all_stages(workspace: Path, manifest: list[dict]) -> list[StageStatus]:
    """Detect status of all pipeline stages."""
    stages = [
        check_stage_0a(workspace, manifest),
        check_stage_0b(workspace, manifest),
        check_stage_0c(workspace, manifest),
        check_stage_0d(workspace, manifest),
        check_stage_1a(workspace, manifest),
        check_stage_1c(workspace, manifest),
        check_stage_2a(workspace, manifest),
        check_stage_2c(workspace, manifest),
        check_stage_3(workspace, manifest),
        check_stage_4(workspace, manifest),
    ]

    # Detect failed stages from manifest error entries
    for entry in manifest:
        if entry.get("status") == "error":
            stage_id = entry.get("step")
            for stage in stages:
                if stage.id == stage_id:
                    stage.status = "failed"
                    stage.error = entry.get("error")
                    break

    return stages


# ---------------------------------------------------------------------------
# Next action determination
# ---------------------------------------------------------------------------


def determine_next_action(stages: list[StageStatus]) -> Optional[NextAction]:
    """Determine single next action to advance pipeline.

    Returns:
        NextAction if there's work to do, None if pipeline complete.
    """
    for stage in stages:
        if stage.status == "pending":
            # Find the pending stage and return its action
            if stage.id == "0a":
                return NextAction(
                    stage="0a",
                    action="run_pre_scan",
                    artifact="raw_signals.json",
                    path=None,
                )
            elif stage.id == "0b":
                return NextAction(
                    stage="0b",
                    action="run_analysis",
                    artifact=None,
                    path=None,
                )
            elif stage.id == "0c":
                return NextAction(
                    stage="0c",
                    action="run_synthesis",
                    artifact=None,
                    path=None,
                )
            elif stage.id == "0d":
                return NextAction(
                    stage="0d",
                    action="review_and_approve",
                    artifact="config.json",
                    path=None,
                )
            elif stage.id == "1a":
                return NextAction(
                    stage="1a",
                    action="run_chunking",
                    artifact="chunks/",
                    path=None,
                )
            elif stage.id == "1c":
                return NextAction(
                    stage="1c",
                    action="run_label_validation",
                    artifact=None,
                    path=None,
                )
            elif stage.id == "2a":
                return NextAction(
                    stage="2a",
                    action="generate_topic_map",
                    artifact="topic_map.json",
                    path=None,
                )
            elif stage.id == "2c":
                return NextAction(
                    stage="2c",
                    action="review_and_approve",
                    artifact="topic_map.json",
                    path=None,
                )
            elif stage.id == "3":
                return NextAction(
                    stage="3",
                    action="run_extraction",
                    artifact="extracts/",
                    path=None,
                )
            elif stage.id == "4":
                return NextAction(
                    stage="4",
                    action="generate_summary",
                    artifact="output/summary.md",
                    path=None,
                )
        elif stage.status == "blocked_on_gate":
            # Human gate is blocking
            return NextAction(
                stage=stage.id,
                action="review_and_approve",
                artifact=stage.gate_artifact,
                path=None,
            )

    return None


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def format_human(stages: list[StageStatus], next_action: Optional[NextAction]) -> str:
    """Format output as human-readable table."""
    no_color = os.environ.get("NO_COLOR") is not None
    console = Console(no_color=no_color)

    # Create table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Stage", style="cyan")
    table.add_column("Name", style="cyan")
    table.add_column("Status")

    for stage in stages:
        symbol = STATUS_SYMBOLS.get(stage.status, "?")
        status_text = stage.status.replace("_", " ").upper()
        if stage.status == "blocked_on_gate" and stage.gate_artifact:
            status_text = f"BLOCKED (waiting for {stage.gate_artifact} approval)"
        elif stage.status == "failed" and stage.error:
            status_text = f"FAILED ({stage.error})"

        table.add_row(f"{symbol} Stage {stage.id}", stage.name, status_text)

    console.print(table)

    # Print next action
    console.print()
    if next_action:
        console.print(f"[bold]Next action:[/bold] {next_action.action.replace('_', ' ').title()}", end=" ")
        if next_action.artifact:
            console.print(f"[yellow]{next_action.artifact}[/yellow]")
        else:
            console.print()
    else:
        console.print("[bold green]Pipeline complete![/bold green]")

    return ""


def format_json(stages: list[StageStatus], next_action: Optional[NextAction]) -> str:
    """Format output as JSON for LLM consumption."""
    stages_data = [
        {
            "id": s.id,
            "name": s.name,
            "status": s.status,
            **({"gate_artifact": s.gate_artifact} if s.gate_artifact else {}),
            **({"error": s.error} if s.error else {}),
        }
        for s in stages
    ]

    blocked_gates = [
        {"stage": s.id, "artifact": s.gate_artifact}
        for s in stages
        if s.status == "blocked_on_gate" and s.gate_artifact
    ]

    failures = [
        {"stage": s.id, "error": s.error} for s in stages if s.status == "failed" and s.error
    ]

    output = {
        "stages": stages_data,
        "blocked_gates": blocked_gates,
        "failures": failures,
    }

    if next_action:
        output["next_action"] = {
            "stage": next_action.stage,
            "action": next_action.action,
            **({"artifact": next_action.artifact} if next_action.artifact else {}),
            **({"path": next_action.path} if next_action.path else {}),
        }

    return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(
    workspace: str = typer.Argument(
        ...,
        help="Path to workspace directory",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON (for LLM consumption)",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        help="Suppress colors in human mode",
    ),
) -> None:
    """Pipeline status reporter: dual-use for humans and LLM."""

    console_err = Console(file=sys.stderr)
    workspace_path = Path(workspace)

    # Validate workspace exists
    if not workspace_path.exists():
        if json_output:
            print(
                json.dumps(
                    {
                        "error": f"Workspace not found: {workspace_path}",
                        "stages": [],
                        "blocked_gates": [],
                        "failures": [],
                    }
                )
            )
        else:
            console_err.print(f"[red]Error:[/red] Workspace not found: {workspace_path}")
        sys.exit(66)

    # Load manifest
    try:
        manifest = load_manifest(workspace_path)
    except FileNotFoundError:
        if json_output:
            print(
                json.dumps(
                    {
                        "error": "manifest.jsonl not found",
                        "stages": [],
                        "blocked_gates": [],
                        "failures": [],
                    }
                )
            )
        else:
            console_err.print(
                f"[red]Error:[/red] manifest.jsonl not found in {workspace_path}"
            )
        sys.exit(78)
    except ValueError as e:
        if json_output:
            print(
                json.dumps(
                    {
                        "error": str(e),
                        "stages": [],
                        "blocked_gates": [],
                        "failures": [],
                    }
                )
            )
        else:
            console_err.print(f"[red]Error:[/red] {e}")
        sys.exit(78)

    # Detect all stages
    stages = detect_all_stages(workspace_path, manifest)

    # Determine next action
    next_action = determine_next_action(stages)

    # Output
    if json_output:
        output = format_json(stages, next_action)
        print(output)
    else:
        if quiet:
            os.environ["NO_COLOR"] = "1"
        format_human(stages, next_action)

    sys.exit(0)


if __name__ == "__main__":
    app = typer.Typer()
    app.command()(main)
    app()
