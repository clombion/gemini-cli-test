# Stage 0c-0d: Synthesis and Human Gate

## Stage 0c: Synthesis Agent

### Task Overview

The synthesis agent receives all four Stage 0b agent outputs and produces `config.json`, which drives the chunking strategy for downstream processing stages.

**Inputs**:
- Timestamp agent output (findings + recommendations)
- Speaker agent output (findings + recommendations)
- Density agent output (findings + recommendations)
- Format agent output (findings + recommendations)

**Output**:
- `config.json` (must validate against `schemas/stage-0c.json`)

### Synthesis Algorithm

The synthesis agent:

1. Reviews all four agent outputs and their recommendations
2. Extracts specific parameters (chunk_size_words, overlap_words, etc.)
3. Resolves conflicts (if agents recommend different strategies)
4. Decides on the primary chunking strategy
5. Applies coherence validation rules (see below)
6. Produces final `config.json`

### Configuration Parameters

#### `chunking_strategy`

Primary strategy for creating chunks. One of:
- `"timestamp-accumulation"` — accumulate content until next timestamp boundary
- `"turn-accumulation"` — accumulate content until next speaker turn
- `"word-count-fallback"` — accumulate exactly N words regardless of boundaries

**Selection Logic**:
- If timestamp agent finds regular timestamps with >80% coverage → prefer `"timestamp-accumulation"`
- Else if speaker agent confirms named speakers and consistent labels → prefer `"turn-accumulation"`
- Else → use `"word-count-fallback"`

#### `chunk_size_words`

Target word count per chunk. Recommended values:
- **Structured content**: 400-500 words (formal, technical)
- **Conversational content**: 250-350 words (dialogue, interview)
- **Mixed/balanced**: 300 words (default)

Default recommendation comes from density agent. Can be overridden if agents provide explicit guidance.

#### `overlap_words`

Number of words to overlap between consecutive chunks. Recommended values:
- Typically **50% of chunk_size_words** (e.g., if chunk_size=300, overlap=150)
- Provides sufficient context bridge for LLM summarization
- Prevents loss of information at chunk boundaries

Default from density agent; adjust based on content complexity.

#### `speakers_identified`

Boolean indicating whether speakers have been identified (named or clearly labeled).

**Set to `true` if**:
- Speaker agent confirms "named" speaker type
- Speaker labels are consistent and identifiable

**Set to `false` if**:
- No speaker labels present
- Speakers are purely anonymous (e.g., "Speaker 1", "A", "B")
- Speaker labels are inconsistent

Used downstream for speaker-aware processing.

#### `timestamp_strategy`

Strategy for handling timestamps in chunk boundaries. One of:
- `"timestamp-accumulation"` — respect timestamp boundaries
- `"turn-accumulation"` — respect speaker turn boundaries
- `"word-count-fallback"` — ignore boundaries, use word counts

Must match or align with `chunking_strategy`. Typically the same value.

#### `self_consistency_enabled`

Boolean (optional, defaults to `false`). Enable self-consistency checking for high-stakes transcripts.

**Use when**:
- Transcript is critical (legal, medical, sensitive)
- Multiple LLM summaries needed for validation
- User explicitly requests enhanced verification

**Default**: `false` (disabled for performance)

### Example Config Synthesis

**Input Agents' Recommendations**:

Timestamp agent:
```
Recommendation: Use timestamp-accumulation strategy
Rationale: High regularity (per-segment) with 90% coverage makes timestamps reliable for boundaries
```

Speaker agent:
```
Recommendation: Enable turn-accumulation as secondary strategy
Rationale: Named speakers with consistent labels allow turn-based chunking
```

Density agent:
```
Recommendation: chunk_size_words = 300, overlap_words = 150
Rationale: Mixed content style (WPM 130) suggests balanced chunk size
```

Format agent:
```
Recommendation: No blocking issues
Rationale: Encoding is clean UTF-8; line endings consistent
```

**Synthesis Output**:

```json
{
  "chunking_strategy": "timestamp-accumulation",
  "chunk_size_words": 300,
  "overlap_words": 150,
  "speakers_identified": true,
  "timestamp_strategy": "timestamp-accumulation",
  "self_consistency_enabled": false
}
```

---

## Coherence Validation (Stage 0c)

Before config is finalized, four **deterministic validation checks** run. These are not LLM-based; they are logical rules that flag inconsistencies.

### Coherence Check 1: Timestamp-Strategy Mismatch

**Rule**: If `timestamp_strategy` is `"word-count-fallback"` but timestamp agent reported `"per-utterance"` or `"fixed-interval"` regularity, flag for human review.

**Rationale**: This indicates timestamps exist and are regular, but the config ignores them. This might be intentional (user wants simple word-count), but it's worth surfacing.

**Action**:
- Log warning: "Timestamps are regular but strategy uses word-count fallback. Confirm this is intentional."
- Do NOT block config; proceed to human gate with flag

### Coherence Check 2: Speaker Inconsistency

**Rule**: If speaker agent reported `consistency_check = false` (inconsistent labeling) but config has `speakers_identified = true`, flag for human review.

**Rationale**: Inconsistent speaker labels mean speaker identification may be unreliable downstream.

**Action**:
- Log warning: "Speaker labels are inconsistent but config marks speakers as identified. Review speaker data before relying on it."
- Do NOT block config; proceed to human gate with flag

### Coherence Check 3: Excessive Segment Count

**Rule**: If estimated segment count implies >200 chunks (e.g., 15,000 words at 75 words/chunk), warn about downstream processing complexity.

**Rationale**: Very long transcripts with many chunks may be slow to process; user should be aware.

**Calculation**:
```
estimated_chunks = character_count / (chunk_size_words * 4.5)
(assumes 4.5 characters per word average)

if estimated_chunks > 200:
  flag warning
```

**Action**:
- Log info: "Transcript will generate ~X chunks. Processing may take time."
- Do NOT block; informational only

### Coherence Check 4: Format Issues Flag

**Rule**: If format agent detected blocking issues (encoding problems, ambiguous timestamps, etc.) and recommended human review, config must not finalize until reviewed.

**Rationale**: Format problems can break downstream parsing; they must be addressed before processing.

**Blocking Issues**:
- Non-UTF8 encoding detected
- Ambiguous or conflicting timestamp formats
- Malformed segment separators that make parsing unreliable

**Non-Blocking Issues**:
- Mixed line endings (can be normalized)
- Some long lines (can be reviewed)
- Minor spacing inconsistencies

**Action**:
- If blocking issues detected: set `flag_for_human_review = true` in config
- Log all format issues with severity
- Proceed to human gate; config requires explicit approval

### Validation Output

Validation produces a report included in the manifest or displayed to user:

```json
{
  "coherence_checks": {
    "timestamp_strategy_mismatch": {
      "status": "warning",
      "message": "Timestamps are regular but strategy uses word-count fallback. Confirm intentional."
    },
    "speaker_inconsistency": {
      "status": "clear",
      "message": null
    },
    "segment_count": {
      "status": "info",
      "message": "Transcript will generate ~150 chunks. Processing time ~2-3 minutes estimated."
    },
    "format_issues": {
      "status": "clear",
      "message": null
    }
  },
  "flag_for_human_review": false,
  "all_checks_passed": true
}
```

---

## Stage 0d: Human Gate Protocol

### Gate Workflow

1. **Display Phase**: Show config.json and validation report to user
2. **Review Phase**: User reviews and decides to approve or edit
3. **Approval Phase**: Once approved, mark in manifest and unlock downstream stages

### Display to User

```
===== Transcript Configuration (Stage 0d) =====

Proposed Configuration:
{
  "chunking_strategy": "timestamp-accumulation",
  "chunk_size_words": 300,
  "overlap_words": 150,
  "speakers_identified": true,
  "timestamp_strategy": "timestamp-accumulation",
  "self_consistency_enabled": false
}

Coherence Validation:
✓ Timestamp strategy consistent with detected regularity
✓ Speaker consistency verified
ℹ Estimated ~150 chunks (processing time ~2-3 minutes)
✓ No blocking format issues

===== End Configuration =====

Please review the configuration above.

Options:
1. Reply "approved" to accept this configuration
2. Edit config.json directly and reply with the modified JSON
3. Ask clarifying questions about specific parameters
```

### User Interactions

#### Option A: Direct Approval

User replies: `approved`

System:
1. Validates config.json against `schemas/stage-0c.json`
2. Marks manifest with `step: "0d"`, `file: "config.json"`, `inputs: { approval: true }`
3. Logs timestamp of approval
4. Signals downstream stages (1a, etc.) that 0d is complete

#### Option B: User Edits Config

User modifies and replies with:
```json
{
  "chunking_strategy": "turn-accumulation",
  "chunk_size_words": 400,
  "overlap_words": 200,
  "speakers_identified": true,
  "timestamp_strategy": "turn-accumulation",
  "self_consistency_enabled": false
}
```

System:
1. Validates edited config against `schemas/stage-0c.json`
2. If valid: marks manifest with approved config + user modification note
3. If invalid: returns validation errors and asks for correction
4. Re-displays validation report with new config

#### Option C: Questions or Clarification

User asks: "Why is chunk_size 300 and not 400?"

System:
1. Displays rationale from density agent:
   ```
   Density agent recommended 300 words based on:
   - Estimated content WPM: 130 (mixed style)
   - Character count: 12,500 / estimated duration 34 minutes
   - 300-word chunks balance semantic coherence with context window
   ```
2. Offers to adjust if user wants different value
3. Returns to approval state

### Handling Flagged Incoherence

If coherence validation flagged issues (warning or blocking), display prominently:

```
===== ⚠️ CONFIGURATION ALERTS =====

Warning: Timestamp Strategy Mismatch
  Timestamps appear regular (per-segment) but strategy is word-count-fallback.
  Is this intentional? If timestamps should drive boundaries, choose
  "timestamp-accumulation" instead.

No Blocking Issues

Please confirm the configuration is correct, or suggest changes.
===== End Alerts =====
```

User must either:
- Confirm config is correct despite warnings (approval)
- Modify config to resolve the warning
- Provide explanation of why the inconsistency is intentional

### Approval Marker in Manifest

Once approved, manifest record:

```json
{
  "step": "0d",
  "stage": "human_gate",
  "file": "config.json",
  "inputs": {
    "approval": true,
    "approved_at": "2026-03-11T14:32:15Z",
    "approved_by": "user",
    "modifications": false,
    "coherence_flags": []
  },
  "outputs": {
    "config_file": "config.json",
    "validation_status": "passed"
  }
}
```

Or, if user made modifications:

```json
{
  "step": "0d",
  "inputs": {
    "approval": true,
    "approved_at": "2026-03-11T14:32:15Z",
    "modifications": true,
    "original_config": { ...Stage 0c output... },
    "user_edits": [ "chunking_strategy changed from timestamp-accumulation to turn-accumulation" ]
  }
}
```

### Downstream Gate Checking

Any stage (1a, 2a, 3, etc.) that depends on config must check manifest:

```
Before proceeding:
1. Look up manifest for step=0d
2. Verify inputs.approval == true
3. If not approved, halt and inform user: "Configuration approval (stage 0d) not complete"
4. If approved, load config.json and proceed
```

This ensures no stage attempts to process without approved configuration.

---

## Synthesis Parameters Summary

| Parameter | Type | Default | Determination |
|-----------|------|---------|---|
| `chunking_strategy` | enum | Based on timestamps | Timestamp agent if regular; else speaker agent; else word-count-fallback |
| `chunk_size_words` | integer | 300 | Density agent WPM analysis |
| `overlap_words` | integer | 150 (50% of chunk_size) | Density agent or calculated |
| `speakers_identified` | boolean | false | Speaker agent type classification |
| `timestamp_strategy` | enum | Matches chunking_strategy | Derived from timestamp regularity |
| `self_consistency_enabled` | boolean | false | User option only (not synthesized) |

---

## Key Design Principles

1. **Clear Responsibility**: Synthesis agent decides based on agent recommendations. Coherence checks are deterministic rules, not LLM judgments.

2. **Human Authority**: Gate gives user full control to override synthesis. Approval explicit and recorded.

3. **Non-Blocking Warnings**: Issues flag for visibility but don't prevent approval (except true blocking problems).

4. **Downstream Dependency**: Stages 1a+ check manifest for 0d approval before proceeding.

5. **Transparency**: All recommendations, rationales, and synthesis decisions are visible to user for review.

6. **Validation**: All configs must validate against `schemas/stage-0c.json` before approval.
