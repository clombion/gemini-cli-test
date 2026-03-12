#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "jsonschema>=4.0.0",
# ]
# ///
"""
Schema validator with rationale quality checks for write-transcript-summary pipeline.

Usage: python validate_schema.py WORKSPACE --stage STAGE_ID [--file FILE]

Schemas and vocab are resolved relative to the skill directory (../schemas/, ../vocab.json),
not the workspace. Data files are found in stage-specific workspace subdirectories.
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import jsonschema


# Exit codes
EXIT_SUCCESS = 0
EXIT_VALIDATION_ERROR = 65
EXIT_NO_INPUT = 66
EXIT_USAGE = 2

# Skill directory is one level up from scripts/
SKILL_DIR = Path(__file__).resolve().parent.parent

# Stage ID → workspace subdirectory mapping
STAGE_DATA_DIRS: Dict[str, str] = {
    '0b': 'analysis',
    '0c': '.',         # config.json lives at workspace root
    '1a': 'labels',    # LLM-filled label records
    '2a': 'topics',
    '2b': 'topics',
    '3': 'extracts',
    '4': 'output',
}


def load_json_file(filepath: Path) -> Any:
    """Load JSON from file, return parsed object or None if invalid."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        return None
    except FileNotFoundError:
        return None


def load_schema(stage_id: str) -> Optional[Dict[str, Any]]:
    """Load schema for the given stage from the skill directory."""
    schema_path = SKILL_DIR / 'schemas' / f'stage-{stage_id}.json'
    return load_json_file(schema_path)


def load_vocab() -> Optional[Dict[str, List[str]]]:
    """Load vocab.json from the skill directory."""
    vocab_path = SKILL_DIR / 'vocab.json'
    return load_json_file(vocab_path)


def get_stage_files(workspace: Path, stage_id: str) -> List[Path]:
    """Get all JSON files for a stage from the correct workspace subdirectory."""
    subdir = STAGE_DATA_DIRS.get(stage_id)
    if subdir is None:
        return []

    if subdir == '.':
        # Stage 0c: config.json at workspace root
        config_path = workspace / 'config.json'
        return [config_path] if config_path.exists() else []

    data_dir = workspace / subdir
    if not data_dir.exists():
        return []

    files = sorted(data_dir.glob('*.json'))
    return files


def validate_json_parse(data: Any) -> Tuple[bool, str]:
    """Check if input is valid JSON. Return (is_valid, error_msg)."""
    if data is None:
        return False, "Invalid JSON: failed to parse"
    return True, ""


def validate_against_schema(
    data: Dict[str, Any],
    schema: Dict[str, Any]
) -> List[str]:
    """
    Validate data against JSON schema.
    Return list of validation errors (empty if valid).
    """
    errors = []
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as e:
        errors.append(f"Schema validation: {e.message}")
    except jsonschema.SchemaError as e:
        errors.append(f"Schema error: {e.message}")

    return errors


def check_additional_properties(
    data: Dict[str, Any],
    schema: Dict[str, Any]
) -> List[str]:
    """
    Check for additionalProperties violations.
    Return list of errors (empty if all properties allowed or none extra).
    """
    errors = []

    # Only check if schema explicitly forbids additionalProperties
    if schema.get('additionalProperties') is False:
        allowed_props = set(schema.get('properties', {}).keys())
        actual_props = set(data.keys())
        extra = actual_props - allowed_props

        if extra:
            errors.append(f"additionalProperties: extra properties not allowed: {sorted(extra)}")

    return errors


def check_required_fields(
    data: Dict[str, Any],
    schema: Dict[str, Any]
) -> List[str]:
    """Check for required fields. Return list of errors."""
    errors = []
    required = schema.get('required', [])

    for field in required:
        if field not in data:
            errors.append(f"Required field missing: '{field}'")

    return errors


def check_controlled_vocabulary(
    data: Dict[str, Any],
    vocab: Dict[str, List[str]]
) -> List[str]:
    """
    Check enum fields against controlled vocabulary.
    Return list of vocabulary violations.
    """
    errors = []

    # Check conversational_function if present
    if 'conversational_function' in data:
        allowed = vocab.get('conversational_function', [])
        value = data['conversational_function']
        if allowed and value not in allowed:
            errors.append(
                f"Controlled vocabulary violation: conversational_function='{value}' "
                f"not in {allowed}"
            )

    return errors


def check_empty_rationale_fields(data: Dict[str, Any]) -> List[str]:
    """
    Check for empty rationale fields (minLength=20 fields).
    Return list of errors.
    """
    errors = []

    # Fields that have minLength=20 in rationale-bearing stages
    rationale_fields = [
        'topic_tags_rationale',
        'entities_rationale',
        'function_rationale',
        'intent_rationale',
        'answer_rationale',
    ]

    for field in rationale_fields:
        if field in data:
            value = data[field]
            if not isinstance(value, str) or len(value) < 20:
                errors.append(f"Empty/short rationale: '{field}' must be >= 20 chars")

    return errors


def check_re_derivation(
    original_data: Dict[str, Any],
    schema: Dict[str, Any]
) -> List[str]:
    """
    Check if scaffold fields have been modified (re-derivation failure).
    Scaffold fields are marked with "scaffold field, deterministic" in description.
    Return list of errors.
    """
    errors = []

    # Parse scaffold fields from schema descriptions
    scaffold_fields = []
    for prop_name, prop_schema in schema.get('properties', {}).items():
        desc = prop_schema.get('description', '')
        if 'scaffold field' in desc and 'deterministic' in desc:
            scaffold_fields.append(prop_name)

    # For now, we can only check presence of scaffold fields, not their modification
    # since we don't have original values. A more complete implementation would
    # compare against a stored original.
    for field in scaffold_fields:
        if field not in original_data:
            # Missing scaffold field could indicate re-derivation
            pass

    return errors


def check_minimum_meaningful_length(
    data: Dict[str, Any],
    stage_id: str
) -> List[Dict[str, Any]]:
    """
    Detect rationale fields with <20 chars (warning, not error).
    Only applies to stages 1a, 2a, 3.
    Return list of warnings.
    """
    warnings = []

    if stage_id not in ['1a', '2a', '3']:
        return warnings

    rationale_fields = [
        'topic_tags_rationale',
        'entities_rationale',
        'function_rationale',
        'intent_rationale',
        'answer_rationale',
    ]

    short_rationales = []
    for field in rationale_fields:
        if field in data:
            value = data[field]
            if isinstance(value, str) and len(value) < 20:
                short_rationales.append(f"{field} ({len(value)} chars)")

    if short_rationales:
        warnings.append({
            'check': 'minimum_meaningful_length',
            'detail': f"Rationale fields with <20 chars: {', '.join(short_rationales)}",
            'affected_count': len(short_rationales)
        })

    return warnings


def check_fatigue_detection(
    all_data: List[Dict[str, Any]],
    stage_id: str
) -> List[Dict[str, Any]]:
    """
    Detect if mean rationale length in second half < 70% of first half.
    Only applies to stages 1a, 2a, 3.
    Return list of warnings.
    """
    warnings = []

    if stage_id not in ['1a', '2a', '3'] or len(all_data) < 2:
        return warnings

    rationale_fields = [
        'topic_tags_rationale',
        'entities_rationale',
        'function_rationale',
        'intent_rationale',
        'answer_rationale',
    ]

    # Collect all rationale lengths
    rationale_lengths = []
    for data in all_data:
        for field in rationale_fields:
            if field in data:
                value = data[field]
                if isinstance(value, str):
                    rationale_lengths.append(len(value))

    if len(rationale_lengths) < 2:
        return warnings

    # Split into halves
    midpoint = len(rationale_lengths) // 2
    first_half = rationale_lengths[:midpoint]
    second_half = rationale_lengths[midpoint:]

    if not first_half or not second_half:
        return warnings

    mean_first = sum(first_half) / len(first_half)
    mean_second = sum(second_half) / len(second_half)

    # Check if second half < 70% of first half
    if mean_first > 0:
        ratio = mean_second / mean_first
        if ratio < 0.70:
            drop_pct = int((1 - ratio) * 100)
            warnings.append({
                'check': 'fatigue_detection',
                'detail': (
                    f"Mean rationale length drops {drop_pct}% from first half "
                    f"({mean_first:.1f} chars) to second half ({mean_second:.1f} chars)"
                ),
                'affected_count': len(second_half)
            })

    return warnings


def check_dominant_value_detection(
    all_data: List[Dict[str, Any]],
    stage_id: str
) -> List[Dict[str, Any]]:
    """
    Detect if any conversational_function value appears >50% of batch.
    Only applies to stages 1a, 2a, 3.
    Return list of warnings.
    """
    warnings = []

    if stage_id not in ['1a', '2a', '3'] or len(all_data) < 2:
        return warnings

    # Count conversational_function values
    function_counts: Dict[str, int] = {}
    total_with_function = 0

    for data in all_data:
        if 'conversational_function' in data:
            value = data['conversational_function']
            function_counts[value] = function_counts.get(value, 0) + 1
            total_with_function += 1

    if total_with_function == 0:
        return warnings

    # Check if any value > 50%
    for value, count in function_counts.items():
        percentage = (count / total_with_function) * 100
        if percentage > 50:
            warnings.append({
                'check': 'dominant_value_detection',
                'detail': (
                    f"conversational_function='{value}' appears in {count}/{total_with_function} "
                    f"records ({percentage:.1f}%)"
                ),
                'affected_count': count
            })

    return warnings


def validate_file(
    filepath: Path,
    schema: Dict[str, Any],
    vocab: Dict[str, List[str]]
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Validate a single JSON file.
    Return (errors, warnings).
    """
    errors = []
    warnings = []

    # Load and parse JSON
    data = load_json_file(filepath)
    if data is None:
        errors.append(f"Failed to parse JSON from {filepath.name}")
        return errors, warnings

    if not isinstance(data, dict):
        errors.append(f"Expected JSON object, got {type(data).__name__}")
        return errors, warnings

    # Hard errors
    errors.extend(validate_against_schema(data, schema))
    errors.extend(check_additional_properties(data, schema))
    errors.extend(check_required_fields(data, schema))
    errors.extend(check_controlled_vocabulary(data, vocab))
    errors.extend(check_empty_rationale_fields(data))
    errors.extend(check_re_derivation(data, schema))

    return errors, warnings


def validate_batch(
    workspace: Path,
    stage_id: str,
    schema: Dict[str, Any],
    vocab: Dict[str, List[str]],
    file_to_validate: Optional[Path] = None
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Validate files for a stage.
    If file_to_validate is provided, only validate that file.
    Otherwise validate all files for the stage.
    Return (errors, warnings).
    """
    errors = []
    warnings_list = []

    # Determine which files to validate
    if file_to_validate:
        if not file_to_validate.exists():
            errors.append(f"File not found: {file_to_validate}")
            return errors, warnings_list
        files_to_check = [file_to_validate]
    else:
        files_to_check = get_stage_files(workspace, stage_id)
        if not files_to_check:
            errors.append(f"No data files found for stage {stage_id}")
            return errors, warnings_list

    # Load all data
    all_data = []
    for filepath in files_to_check:
        data = load_json_file(filepath)
        if data is None:
            errors.append(f"Failed to parse JSON: {filepath.name}")
            continue

        if not isinstance(data, dict):
            errors.append(f"Expected JSON object in {filepath.name}, got {type(data).__name__}")
            continue

        # Validate individual file
        file_errors, file_warnings = validate_file(filepath, schema, vocab)
        errors.extend(file_errors)
        warnings_list.extend(file_warnings)

        all_data.append(data)

    # Batch-level quality checks (fatigue, dominant-value detection)
    if all_data and not errors:
        warnings_list.extend(check_fatigue_detection(all_data, stage_id))
        warnings_list.extend(check_dominant_value_detection(all_data, stage_id))

    return errors, warnings_list


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Validate JSON files against schema with rationale quality checks'
    )
    parser.add_argument('workspace', help='Path to workspace directory')
    parser.add_argument('--stage', required=True, help='Stage ID (0b, 0c, 1a, 2a, 2b, 3, 4)')
    parser.add_argument('--file', help='Optional: specific file to validate')

    args = parser.parse_args()

    workspace = Path(args.workspace)
    stage_id = args.stage
    file_arg = args.file

    # Validate stage ID
    valid_stages = ['0b', '0c', '1a', '2a', '2b', '3', '4']
    if stage_id not in valid_stages:
        print(json.dumps({
            'valid': False,
            'errors': [f"Invalid stage ID: {stage_id}. Must be one of {valid_stages}"],
            'warnings': []
        }))
        return EXIT_USAGE

    # Check workspace exists
    if not workspace.exists():
        print(json.dumps({
            'valid': False,
            'errors': [f"Workspace not found: {workspace}"],
            'warnings': []
        }))
        return EXIT_VALIDATION_ERROR

    # Load schema from skill directory
    schema = load_schema(stage_id)
    if schema is None:
        print(json.dumps({
            'valid': False,
            'errors': [f"Schema not found for stage {stage_id} in {SKILL_DIR / 'schemas'}"],
            'warnings': []
        }))
        return EXIT_VALIDATION_ERROR

    # Load vocab from skill directory
    vocab = load_vocab()
    if vocab is None:
        print(json.dumps({
            'valid': False,
            'errors': [f"vocab.json not found in {SKILL_DIR}"],
            'warnings': []
        }))
        return EXIT_VALIDATION_ERROR

    # Validate
    file_to_validate = Path(file_arg) if file_arg else None
    errors, warnings = validate_batch(workspace, stage_id, schema, vocab, file_to_validate)

    # Generate output
    output = {
        'valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings
    }

    print(json.dumps(output))

    # Exit codes
    if errors:
        return EXIT_VALIDATION_ERROR

    return EXIT_SUCCESS


if __name__ == '__main__':
    sys.exit(main())
