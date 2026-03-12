#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "typer>=0.9.0",
#     "rich>=13.0.0",
#     "jsonschema>=4.18.0",
# ]
# ///
"""Validate webscraper-schema/sitemap.schema.json against the WebScraper.io source.

Extracts ground truth (field names, enums, constraints) from the webcrack-
deobfuscated devtools_panel.js and compares them to the JSON Schema.  With
--sitemap, also validates an actual sitemap file against the schema.

Prerequisites:
    - uv (to run this script — all Python deps are declared inline)
    - Node.js + pnpm (only needed for --source comparison via webcrack, not for --sitemap validation)

Usage:
    uv run utils/webscraper-schema/validate_schema.py
    uv run utils/webscraper-schema/validate_schema.py --source path/to/deobfuscated.js
    uv run utils/webscraper-schema/validate_schema.py --sitemap fixtures/yakshascans.json
    uv run utils/webscraper-schema/validate_schema.py --json
    uv run utils/webscraper-schema/validate_schema.py --verbose

Exit codes:
    0  All checks pass
    1  Mismatches found between schema and source
    65 Data error (malformed schema or source)
    66 Input file not found
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# Constants & configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE_DIR = SCRIPT_DIR / ".webcrack-out"
DEFAULT_SOURCE_FILE = DEFAULT_SOURCE_DIR / "deobfuscated.js"
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_SCHEMA_FILE = SKILL_DIR / "references" / "sitemap.schema.json"
DEVTOOLS_PANEL = SKILL_DIR.parent / "webscraperio" / "devtools_panel.js"

# Base fields present on every selector (from Selector base class constructor
# + SelectorList.push serialization). These are NOT listed in getFeatures().
BASE_FIELDS = frozenset({"id", "type", "parentSelectors"})

# UI-only fields that appear in getFeatures() but are never serialized.
# They are buttons rendered in the edit panel, not data fields.
UI_ONLY_FIELDS = frozenset({"performActionButton", "dataPreviewButton"})

# Regex patterns for extraction from deobfuscated JS
RE_CONSTRUCTOR_TYPE = re.compile(
    r'this\.type\s*=\s*"((?:Selector|Action)\w+)"'
)
RE_THIS_ASSIGNMENT = re.compile(
    r"this\.(\w+)\s*="
)
RE_GET_FEATURES = re.compile(
    r"getFeatures\(\)\s*\{[^}]*return\s*\[([^\]]*)\]",
    re.DOTALL,
)
RE_GET_HIDDEN_FEATURES = re.compile(
    r"getHiddenFeatures\(\)\s*\{[^}]*return\s*\[([^\]]*)\]",
    re.DOTALL,
)
RE_GET_EXPERIMENTAL_FEATURES = re.compile(
    r"getExperimentalFeatures\(\)\s*\{[^}]*return\s*\[([^\]]*)\]",
    re.DOTALL,
)
RE_STRING_LITERAL = re.compile(r'"([^"]*)"')
RE_TYPE_ENTRY = re.compile(
    r"\{([^}]+)\}",
    re.DOTALL,
)
RE_URL_LIMIT = re.compile(
    r"isUrlLimitNotExceeded.*?\((\d+)\)"
)
RE_ID_MIN_LENGTH = re.compile(
    r"isLengthGreaterOrEqualThan.*?\((\d+)\)"
)
RE_ID_MAX_LENGTH = re.compile(
    r"isLengthLessOrEqualThan.*?\((\d+)\)"
)

# Enum field names and their known values (from UI options + case statements)
KNOWN_ENUMS: dict[str, list[str]] = {
    "multipleType": ["singleColumn", "multipleColumns", "singleColumnWithSeparator"],
    "linkType": ["linkFromHref", "linkFromInnerText", "linkFromAttributes", "linkFromInlineScript", "linkFromRedirect"],
    "paginationType": ["auto", "linkFromHref", "linkFromInlineScript", "linkFromAttributes", "linkFromInnerText", "clickMore", "clickOnce", "linkFromRedirect"],
    "clickType": ["clickOnce", "clickMore"],
    "clickElementUniquenessType": ["uniqueText", "uniqueHTMLText", "uniqueHTML", "uniqueCSSSelector"],
    "clickActionType": ["auto", "real-like-events", "real"],
    "discardInitialElements": ["do-not-discard", "discard-when-click-element-exists", "discard"],
}


# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------

@dataclass
class SelectorTypeInfo:
    """Ground truth about a single selector type extracted from JS source."""
    type_name: str
    features: list[str] = field(default_factory=list)
    hidden_features: list[str] = field(default_factory=list)
    experimental_features: list[str] = field(default_factory=list)
    experimental: bool = False
    deprecated: bool = False

    @property
    def serialized_fields(self) -> set[str]:
        """Fields that appear in exported JSON = base + features + hidden - UI-only."""
        all_fields = (
            set(BASE_FIELDS)
            | set(self.features)
            | set(self.hidden_features)
        ) - UI_ONLY_FIELDS
        return all_fields


@dataclass
class TopLevelConstraints:
    """Constraints on the top-level sitemap object."""
    id_min_length: int | None = None
    id_max_length: int | None = None
    id_pattern: str | None = None
    start_url_max: int | None = None
    selectors_max: int | None = None


@dataclass
class GroundTruth:
    """Everything extracted from the JS source."""
    types: dict[str, SelectorTypeInfo] = field(default_factory=dict)
    type_order: list[str] = field(default_factory=list)
    constraints: TopLevelConstraints = field(default_factory=TopLevelConstraints)


@dataclass
class Mismatch:
    """A single discrepancy between schema and source."""
    category: str
    selector_type: str
    detail: str


# ---------------------------------------------------------------------------
# Pure business logic — extraction
# ---------------------------------------------------------------------------

def _extract_string_list(text: str) -> list[str]:
    """Extract quoted strings from a JS array literal body."""
    return RE_STRING_LITERAL.findall(text)


def _extract_bracket_block(source: str, open_pos: int) -> str:
    """Extract content between matching [ ] starting at open_pos (the '[')."""
    depth = 1
    i = open_pos + 1
    while depth > 0 and i < len(source):
        if source[i] == "[":
            depth += 1
        elif source[i] == "]":
            depth -= 1
        i += 1
    return source[open_pos + 1 : i - 1]


def extract_selector_types_registry(source: str) -> list[SelectorTypeInfo]:
    """Extract the selectorTypes registry array using bracket-counting."""
    m = re.search(r"\.selectorTypes\s*=\s*\[", source)
    if not m:
        return []
    block = _extract_bracket_block(source, m.end() - 1)
    result = []
    for entry in RE_TYPE_ENTRY.finditer(block):
        obj_text = entry.group(1)
        type_m = re.search(r'type:\s*"(\w+)"', obj_text)
        if not type_m:
            continue
        exp_m = re.search(r"experimental:\s*(true|false)", obj_text)
        dep_m = re.search(r"deprecated:\s*(true|false)", obj_text)
        info = SelectorTypeInfo(
            type_name=type_m.group(1),
            experimental=exp_m.group(1) == "true" if exp_m else False,
            deprecated=dep_m.group(1) == "true" if dep_m else False,
        )
        result.append(info)
    return result


def extract_features_for_type(source: str, type_name: str) -> tuple[list[str], list[str], list[str]]:
    """Extract getFeatures, getHiddenFeatures, getExperimentalFeatures for a type.

    Strategy: find the constructor `this.type = "TypeName"`, then scan the
    surrounding module block (~500 lines) for the feature methods.
    """
    # Find constructor location
    pattern = re.compile(rf'this\.type\s*=\s*"{re.escape(type_name)}"')
    match = pattern.search(source)
    if not match:
        return [], [], []

    # Extract a window around the constructor (the class body)
    start = max(0, match.start() - 500)
    end = min(len(source), match.end() + 8000)
    window = source[start:end]

    features = []
    hidden = []
    experimental = []

    m = RE_GET_FEATURES.search(window)
    if m:
        features = _extract_string_list(m.group(1))

    m = RE_GET_HIDDEN_FEATURES.search(window)
    if m:
        hidden = _extract_string_list(m.group(1))

    m = RE_GET_EXPERIMENTAL_FEATURES.search(window)
    if m:
        experimental = _extract_string_list(m.group(1))

    return features, hidden, experimental


def extract_top_level_constraints(source: str) -> TopLevelConstraints:
    """Extract _id validation, startUrl max, selectors max from Yup schemas."""
    constraints = TopLevelConstraints()

    # Find the sitemapIdSchema definition line (the one with .test() chains)
    id_block_match = re.search(
        r"sitemapIdSchema\s*=\s*\w+\.string\(\)\.test\(.+",
        source,
    )
    if id_block_match:
        block = id_block_match.group(0)
        m = RE_ID_MIN_LENGTH.search(block)
        if m:
            constraints.id_min_length = int(m.group(1))
        m = RE_ID_MAX_LENGTH.search(block)
        if m:
            constraints.id_max_length = int(m.group(1))

    # _id pattern from isValidSitemapName function definition
    # JS: t.isValidSitemapName = (e, t) => !!new RegExp(/^[a-zA-Z0-9_\(\)\+\-]+$/).test(e)
    m = re.search(
        r"isValidSitemapName\s*=\s*\([^)]*\)\s*=>.*?RegExp\((/[^/]+/)\)",
        source,
    )
    if m:
        pattern = m.group(1)[1:-1]  # strip slashes
        constraints.id_pattern = pattern

    # startUrl limit
    m = RE_URL_LIMIT.search(source)
    if m:
        constraints.start_url_max = int(m.group(1))

    # selectors max — find .max(N) near selectors schema
    selectors_block = re.search(r"typeErrorMessage\)\.max\((\d+)\)", source)
    if selectors_block:
        constraints.selectors_max = int(selectors_block.group(1))

    return constraints


def extract_ground_truth(source: str) -> GroundTruth:
    """Full extraction pipeline."""
    truth = GroundTruth()

    # 1. Registry
    registry = extract_selector_types_registry(source)
    for info in registry:
        truth.type_order.append(info.type_name)
        truth.types[info.type_name] = info

    # 2. Features per type
    for type_name, info in truth.types.items():
        features, hidden, experimental = extract_features_for_type(source, type_name)
        info.features = features
        info.hidden_features = hidden
        info.experimental_features = experimental

    # 3. Top-level constraints
    truth.constraints = extract_top_level_constraints(source)

    return truth


# ---------------------------------------------------------------------------
# Pure business logic — comparison
# ---------------------------------------------------------------------------

def _resolve_ref(ref: str, schema: dict) -> dict:
    """Resolve a JSON Schema $ref like '#/$defs/SelectorText' to its definition."""
    if not ref.startswith("#/"):
        return {}
    parts = ref[2:].split("/")
    node = schema
    for part in parts:
        node = node.get(part, {})
    return node


def _resolve_one_of_branches(schema: dict) -> list[dict]:
    """Get resolved selector type definitions from oneOf branches."""
    one_of = schema.get("properties", {}).get("selectors", {}).get("items", {}).get("oneOf", [])
    resolved = []
    for branch in one_of:
        if "$ref" in branch:
            resolved.append(_resolve_ref(branch["$ref"], schema))
        else:
            resolved.append(branch)
    return resolved


def compare_type_list(truth: GroundTruth, schema: dict) -> list[Mismatch]:
    """Compare the set of selector types."""
    mismatches = []
    branches = _resolve_one_of_branches(schema)

    schema_types = set()
    for branch in branches:
        props = branch.get("properties", {})
        type_const = props.get("type", {}).get("const")
        if type_const:
            schema_types.add(type_const)

    source_types = set(truth.type_order)

    for t in source_types - schema_types:
        mismatches.append(Mismatch("type_list", t, "In source but missing from schema"))
    for t in schema_types - source_types:
        mismatches.append(Mismatch("type_list", t, "In schema but missing from source"))

    return mismatches


def compare_fields(truth: GroundTruth, schema: dict) -> list[Mismatch]:
    """Compare serialized fields per type."""
    mismatches = []
    branches = _resolve_one_of_branches(schema)

    schema_type_map: dict[str, set[str]] = {}
    for branch in branches:
        props = branch.get("properties", {})
        type_const = props.get("type", {}).get("const")
        if type_const:
            schema_type_map[type_const] = set(props.keys())

    for type_name, info in truth.types.items():
        source_fields = info.serialized_fields
        schema_fields = schema_type_map.get(type_name, set())

        if not schema_fields:
            continue  # Already caught by type_list check

        missing = source_fields - schema_fields
        extra = schema_fields - source_fields

        for f in sorted(missing):
            mismatches.append(Mismatch("fields", type_name, f"Missing from schema: {f}"))
        for f in sorted(extra):
            mismatches.append(Mismatch("fields", type_name, f"Extra in schema (not in source): {f}"))

    return mismatches


def compare_enums(truth: GroundTruth, schema: dict) -> list[Mismatch]:
    """Compare enum values for known enum fields."""
    mismatches = []
    branches = _resolve_one_of_branches(schema)
    defs = schema.get("$defs", {})

    # Collect all enum definitions from schema — both inline and $defs
    schema_enums: dict[str, set[str]] = {}

    # From $defs (named enums)
    for def_name, def_schema in defs.items():
        if "enum" in def_schema:
            schema_enums[def_name] = set(def_schema["enum"])

    # From resolved oneOf branches — find enum fields (inline or via $ref)
    for branch in branches:
        props = branch.get("properties", {})
        for field_name, field_schema in props.items():
            if "enum" in field_schema:
                schema_enums[field_name] = set(field_schema["enum"])
            if "$ref" in field_schema:
                ref_def = _resolve_ref(field_schema["$ref"], schema)
                if "enum" in ref_def:
                    schema_enums[field_name] = set(ref_def["enum"])

    for enum_name, expected_values in KNOWN_ENUMS.items():
        expected = set(expected_values)
        actual = schema_enums.get(enum_name, set())
        if not actual:
            mismatches.append(Mismatch("enums", enum_name, "Enum not found in schema"))
            continue
        missing = expected - actual
        extra = actual - expected
        for v in sorted(missing):
            mismatches.append(Mismatch("enums", enum_name, f"Missing value: {v}"))
        for v in sorted(extra):
            mismatches.append(Mismatch("enums", enum_name, f"Extra value: {v}"))

    return mismatches


def compare_constraints(truth: GroundTruth, schema: dict) -> list[Mismatch]:
    """Compare top-level constraints."""
    mismatches = []
    props = schema.get("properties", {})
    c = truth.constraints

    # _id
    id_schema = props.get("_id", {})
    if c.id_min_length is not None:
        schema_min = id_schema.get("minLength")
        if schema_min != c.id_min_length:
            mismatches.append(Mismatch("constraints", "_id", f"minLength: source={c.id_min_length}, schema={schema_min}"))
    if c.id_max_length is not None:
        schema_max = id_schema.get("maxLength")
        if schema_max != c.id_max_length:
            mismatches.append(Mismatch("constraints", "_id", f"maxLength: source={c.id_max_length}, schema={schema_max}"))
    if c.id_pattern is not None:
        schema_pattern = id_schema.get("pattern")
        if schema_pattern != c.id_pattern:
            mismatches.append(Mismatch("constraints", "_id", f"pattern: source={c.id_pattern}, schema={schema_pattern}"))

    # startUrl
    start_url_schema = props.get("startUrl", {})
    if c.start_url_max is not None:
        schema_max_items = start_url_schema.get("maxItems")
        if schema_max_items != c.start_url_max:
            mismatches.append(Mismatch("constraints", "startUrl", f"maxItems: source={c.start_url_max}, schema={schema_max_items}"))

    # selectors
    selectors_schema = props.get("selectors", {})
    if c.selectors_max is not None:
        schema_max_items = selectors_schema.get("maxItems")
        if schema_max_items != c.selectors_max:
            mismatches.append(Mismatch("constraints", "selectors", f"maxItems: source={c.selectors_max}, schema={schema_max_items}"))

    return mismatches


def compare_all(truth: GroundTruth, schema: dict) -> list[Mismatch]:
    """Run all comparisons."""
    return (
        compare_type_list(truth, schema)
        + compare_fields(truth, schema)
        + compare_enums(truth, schema)
        + compare_constraints(truth, schema)
    )


# ---------------------------------------------------------------------------
# I/O functions
# ---------------------------------------------------------------------------

def run_webcrack(panel_path: Path, output_dir: Path) -> Path:
    """Run webcrack to deobfuscate devtools_panel.js.

    Requires Node.js and pnpm. Only needed for --source comparison mode
    (schema drift detection), never for --sitemap validation.
    """
    # webcrack creates the output dir itself; remove if empty from a prior failed run
    if output_dir.exists() and not any(output_dir.iterdir()):
        output_dir.rmdir()
    try:
        subprocess.run(
            ["pnpm", "dlx", "webcrack", str(panel_path), "-o", str(output_dir)],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise SystemExit(
            "pnpm not found — needed to deobfuscate devtools_panel.js.\n"
            "\n"
            "  To install: npm install -g pnpm  (requires Node.js)\n"
            "  Or skip:    pass --source <path/to/deobfuscated.js> if you already have one\n"
            "  Or:         use --sitemap <file.json> for validation-only mode (no Node.js needed)"
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip()
        raise SystemExit(
            f"webcrack failed:\n  {stderr}\n"
            "\n"
            "  If output dir already exists, delete it: rm -rf {output_dir}\n"
            "  Or pass --source <path/to/deobfuscated.js> to skip webcrack"
        )
    return output_dir / "deobfuscated.js"


def load_source(path: Path) -> str:
    """Read the deobfuscated JS source."""
    if not path.exists():
        raise FileNotFoundError(f"Source not found: {path}")
    return path.read_text(encoding="utf-8")


def load_schema(path: Path) -> dict:
    """Load and parse the JSON Schema."""
    if not path.exists():
        raise FileNotFoundError(f"Schema not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in schema: {e}")


def validate_sitemap_file(schema: dict, sitemap_path: Path) -> list[str]:
    """Validate a sitemap JSON file against the schema. Returns error messages."""
    from jsonschema import Draft202012Validator

    if not sitemap_path.exists():
        raise FileNotFoundError(f"Sitemap not found: {sitemap_path}")

    try:
        sitemap = json.loads(sitemap_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"]

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(sitemap), key=lambda e: list(e.path))
    return [f"{'.'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in errors]


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def display_results(
    mismatches: list[Mismatch],
    console: Console,
    verbose: bool = False,
) -> None:
    """Render mismatches as a rich table."""
    if not mismatches:
        console.print("[bold green]All checks passed.[/bold green]")
        return

    table = Table(title=f"Schema Mismatches ({len(mismatches)})")
    table.add_column("Category", style="cyan")
    table.add_column("Type/Field", style="yellow")
    table.add_column("Detail", style="red")

    for m in mismatches:
        table.add_row(m.category, m.selector_type, m.detail)

    console.print(table)


def display_ground_truth(truth: GroundTruth, console: Console) -> None:
    """Show extracted ground truth for debugging."""
    table = Table(title="Extracted Ground Truth")
    table.add_column("Type", style="cyan")
    table.add_column("Serialized Fields", style="white")
    table.add_column("Experimental Features", style="yellow")
    table.add_column("Flags", style="dim")

    for type_name in truth.type_order:
        info = truth.types[type_name]
        flags = []
        if info.experimental:
            flags.append("experimental")
        if info.deprecated:
            flags.append("deprecated")
        table.add_row(
            type_name,
            ", ".join(sorted(info.serialized_fields)),
            ", ".join(info.experimental_features) or "-",
            ", ".join(flags) or "-",
        )

    console.print(table)

    console.print(f"\n[bold]Top-level constraints:[/bold]")
    c = truth.constraints
    console.print(f"  _id: minLength={c.id_min_length}, maxLength={c.id_max_length}, pattern={c.id_pattern}")
    console.print(f"  startUrl maxItems: {c.start_url_max}")
    console.print(f"  selectors maxItems: {c.selectors_max}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="validate_schema",
    help="Validate webscraper sitemap JSON Schema against the WebScraper.io source.",
    no_args_is_help=False,
)


@app.command()
def main(
    source: Annotated[
        Path | None,
        typer.Option(
            "--source", "-s",
            help="Path to deobfuscated.js. If omitted, runs webcrack automatically.",
        ),
    ] = None,
    schema: Annotated[
        Path,
        typer.Option(
            "--schema",
            help="Path to sitemap.schema.json.",
        ),
    ] = DEFAULT_SCHEMA_FILE,
    sitemap: Annotated[
        Path | None,
        typer.Option(
            "--sitemap",
            help="Optional sitemap JSON file to validate against the schema.",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show extracted ground truth."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Only show errors."),
    ] = False,
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Output results as JSON."),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option("--no-color", help="Disable color output."),
    ] = False,
) -> None:
    """Compare sitemap.schema.json against the WebScraper.io deobfuscated source."""
    force_terminal = not no_color and "NO_COLOR" not in os.environ
    console = Console(
        no_color=no_color or "NO_COLOR" in os.environ,
        force_terminal=force_terminal,
    )

    # Load schema (always needed)
    try:
        schema_data = load_schema(schema)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=66)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=65)

    # Source comparison (optional — skipped if no source available and not requested)
    mismatches: list[Mismatch] = []
    run_source_comparison = True

    source_path = source or DEFAULT_SOURCE_FILE
    if not source_path.exists():
        if source is not None:
            # Explicitly requested source not found — error
            console.print(f"[red]Source file not found: {source_path}[/red]")
            raise typer.Exit(code=66)
        if DEVTOOLS_PANEL.exists():
            if not quiet:
                console.print(f"Running webcrack on {DEVTOOLS_PANEL}...")
            source_path = run_webcrack(DEVTOOLS_PANEL, DEFAULT_SOURCE_DIR)
        else:
            # No source available — skip comparison, sitemap-only mode
            run_source_comparison = False
            if sitemap is None:
                console.print("[red]No source file and no --sitemap provided. Nothing to do.[/red]")
                console.print("Use --source <deobfuscated.js> or --sitemap <file.json>")
                raise typer.Exit(code=66)

    if run_source_comparison:
        try:
            js_source = load_source(source_path)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=66)

        truth = extract_ground_truth(js_source)

        if verbose and not output_json:
            display_ground_truth(truth, console)
            console.print()

        mismatches = compare_all(truth, schema_data)

    # Sitemap validation
    sitemap_errors: list[str] = []
    if sitemap is not None:
        try:
            sitemap_errors = validate_sitemap_file(schema_data, sitemap)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=66)

    # Output
    if output_json:
        result: dict[str, Any] = {
            "sitemap_errors": sitemap_errors,
            "pass": len(mismatches) == 0 and len(sitemap_errors) == 0,
        }
        if run_source_comparison:
            result["schema_mismatches"] = [
                {"category": m.category, "type": m.selector_type, "detail": m.detail}
                for m in mismatches
            ]
        console.print_json(json.dumps(result))
    else:
        if run_source_comparison and (not quiet or mismatches):
            display_results(mismatches, console, verbose)

        if sitemap is not None:
            if sitemap_errors:
                console.print(f"\n[bold red]Sitemap validation errors ({len(sitemap_errors)}):[/bold red]")
                for err in sitemap_errors:
                    console.print(f"  {err}")
            elif not quiet:
                console.print(f"\n[bold green]Sitemap {sitemap.name} is valid.[/bold green]")

    # Exit code
    if mismatches or sitemap_errors:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
