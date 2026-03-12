# Stage 0b: Parallel Analysis Agents

## Overview

Stage 0b executes four independent analysis agents in parallel, each reading `raw_signals.json` and producing structured findings and recommendations. These agents operate concurrently and their outputs feed into Stage 0c (synthesis).

Each agent analyzes different aspects of the raw transcript to inform the chunking strategy:
- **Timestamp Agent**: timestamp presence, format, and regularity
- **Speaker Agent**: speaker labeling and consistency
- **Density Agent**: content volume and estimated processing characteristics
- **Format Agent**: structural issues and encoding quality

All four agents operate on the same input (`raw_signals.json`) but focus on different signals. Their outputs must conform to `schemas/stage-0b.json`.

---

## Timestamp Agent

### Input Signals

Reads from `raw_signals.json`:
- `timestamp_present`: boolean indicating if timestamps exist
- `timestamp_format`: string describing the format pattern
- `timestamp_regularity`: classification of timing pattern
- `estimated_timestamp_coverage`: percentage of content with timestamps

### Output Schema

```json
{
  "agent_type": "timestamp",
  "findings": "human-readable summary of timestamp patterns",
  "recommendations": [
    {
      "recommendation": "specific chunking action",
      "rationale": "minimum 10 characters explaining why"
    }
  ]
}
```

### Task Description

Analyze timestamp patterns to determine optimal chunking boundaries. If timestamps are regular and present, they should drive chunk boundaries. If absent or sparse, fall back to word-count strategies.

### Analysis Focus

**Format Examples**:
- `"hh:mm:ss"` — full 24-hour format (e.g., 14:32:15)
- `"mm:ss"` — minutes and seconds only (e.g., 32:15)
- `"bracketed_hhmm"` — bracketed time notation (e.g., [14:32])
- `"sparse_irregular"` — timestamps present but not at regular intervals
- `"absent"` — no timestamps in transcript

**Regularity Examples**:
- `"per-utterance"` — each speaker turn has a timestamp
- `"fixed-interval"` — timestamps at regular time intervals (every 30s, etc.)
- `"sparse"` — timestamps scattered, no clear pattern
- `"absent"` — no timestamps

### Sample Output

```json
{
  "agent_type": "timestamp",
  "findings": "Timestamps present in mm:ss format at regular intervals (approximately every 30-45 seconds). Coverage is 85% of content segments.",
  "recommendations": [
    {
      "recommendation": "Use timestamp-accumulation strategy with accumulated_ms boundaries aligned to nearest timestamp",
      "rationale": "High regularity and good coverage makes timestamps reliable for chunk boundaries, reducing word-count dependency and improving semantic coherence"
    },
    {
      "recommendation": "Set overlap_words to 150 to catch cross-timestamp context",
      "rationale": "Timestamp intervals are consistent, so fixed overlap handles bridging between timestamp-based chunks effectively"
    }
  ]
}
```

---

## Speaker Agent

### Input Signals

Reads from `raw_signals.json`:
- `speaker_label_present`: boolean indicating speaker labels exist
- `distinct_speakers`: count of unique speakers
- `speaker_type`: classification ("named", "anonymous", "absent")

### Output Schema

```json
{
  "agent_type": "speaker",
  "findings": "summary of speaker structure",
  "recommendations": [
    {
      "recommendation": "specific action",
      "rationale": "minimum 10 characters"
    }
  ]
}
```

### Task Description

Evaluate speaker structure to determine if turn-accumulation is viable and whether speaker transitions should influence chunk boundaries.

### Analysis Focus

**Speaker Types**:
- `"named"` — speakers identified by name (e.g., "Alice:", "Bob:")
- `"anonymous"` — speakers labeled but not named (e.g., "Speaker 1:", "A:", "B:")
- `"absent"` — no speaker labels in transcript

**Consistency Checks**:
- Are speaker labels applied consistently throughout?
- Do speaker transitions align with natural semantic breaks?
- Are speaker labels clear and machine-parseable?

### Sample Output

```json
{
  "agent_type": "speaker",
  "findings": "Three named speakers consistently labeled (Alice, Bob, Charlie). Speaker transitions occur throughout transcript. Labels are consistently formatted as 'Speaker:' with no variations.",
  "recommendations": [
    {
      "recommendation": "Enable turn-accumulation as secondary strategy with speaker transitions as soft boundaries",
      "rationale": "Named speakers with consistent labeling allow reliable turn-based chunking, which respects conversational structure and can improve chunk coherence"
    },
    {
      "recommendation": "Mark speakers_identified as true in config",
      "rationale": "Named speakers are identifiable, so downstream processing can use speaker context for enhanced summarization"
    }
  ]
}
```

---

## Density Agent

### Input Signals

Reads from `raw_signals.json`:
- `character_count`: total character count
- `line_count`: total number of lines
- `estimated_segment_count`: estimated number of utterances/segments

### Output Schema

```json
{
  "agent_type": "density",
  "findings": "summary of content volume and style",
  "recommendations": [
    {
      "recommendation": "specific parameter recommendation",
      "rationale": "minimum 10 characters"
    }
  ]
}
```

### Task Description

Estimate document density and recommend chunk sizes based on content style and volume. Calculate words-per-minute for spoken content and adjust chunk recommendations accordingly.

### Calculations

**Estimated Duration**:
```
estimated_duration_minutes = estimated_segment_count / 150
(assumes roughly 150 utterances per hour of speech)
```

**Words Per Minute (WPM)**:
```
estimated_wpm = character_count / (estimated_duration_minutes * 180)
(assumes roughly 5 chars per word, 180 words per minute baseline)
```

**Content Style Classification**:
- `"structured"` — formal, technical, or lecture-style (WPM > 140)
- `"conversational"` — dialogue, interview, casual (WPM < 120)
- `"mixed"` — varies throughout (between 120-140)

### Chunk Size Recommendation

- **Structured content** (formal): 400-500 words (allows complex concepts to stay together)
- **Conversational content** (dialogue): 250-350 words (respects conversational turns better)
- **Mixed content**: 300 words (balanced default)

### Sample Output

```json
{
  "agent_type": "density",
  "findings": "Document contains 12,500 characters across 85 segments, estimated duration ~34 minutes. Calculated WPM ~130, suggesting mixed content style with technical and conversational elements.",
  "recommendations": [
    {
      "recommendation": "Set chunk_size_words to 300 (default mixed-style recommendation)",
      "rationale": "WPM of 130 indicates mixed content; 300-word chunks balance semantic coherence with manageable context for summarization"
    },
    {
      "recommendation": "Set overlap_words to 150",
      "rationale": "Half of chunk_size provides sufficient context bridge between chunks without excessive redundancy in high-density content"
    },
    {
      "recommendation": "Monitor for very long utterances (>500 words consecutive) that may need splitting",
      "rationale": "Density analysis should identify outlier segments that might benefit from forced chunk boundaries"
    }
  ]
}
```

---

## Format Agent

### Input Signals

Reads from `raw_signals.json`:
- `warnings`: array of detected issues (long lines, mixed line endings, encoding problems, etc.)
- Any structural anomalies detected during signal collection

### Output Schema

```json
{
  "agent_type": "format",
  "findings": "summary of format issues",
  "recommendations": [
    {
      "recommendation": "specific action",
      "rationale": "minimum 10 characters"
    }
  ]
}
```

### Task Description

Identify structural and encoding issues that might affect downstream processing. Flag edge cases that require human review before configuration is finalized.

### Issues to Detect

- **Mixed line endings**: CRLF + LF in same file
- **Long lines**: lines >500 characters (may indicate transcript format anomalies)
- **Encoding issues**: non-UTF8 characters, invalid encoding markers
- **Ambiguous timestamps**: multiple conflicting timestamp formats
- **Inconsistent speaker format**: speaker labels vary throughout
- **Sparse or malformed separators**: missing or irregular chunk separators

### Sample Output

```json
{
  "agent_type": "format",
  "findings": "Detected mixed line endings (85% LF, 15% CRLF) and three lines exceeding 400 characters. Encoding is valid UTF-8. Timestamp format is consistent (mm:ss).",
  "recommendations": [
    {
      "recommendation": "Normalize line endings to LF before processing",
      "rationale": "Mixed line endings can cause inconsistent segment boundary detection; normalization ensures predictable parsing"
    },
    {
      "recommendation": "Review the three long lines (>400 chars) for potential embedded breaks",
      "rationale": "Unusually long lines may indicate missing segment markers or transcription errors that should be corrected at source"
    },
    {
      "recommendation": "Flag for human review due to line ending inconsistency",
      "rationale": "While not critical, mixed line endings suggest file may have been edited across different systems; confirm source integrity"
    }
  ]
}
```

---

## Agent Execution

### Prompt Structure for Each Agent

Each agent receives:

```
You are a specialized analysis agent for transcript signal processing.

Your role: [Timestamp|Speaker|Density|Format] analysis

Input (raw_signals.json):
[full raw_signals.json content]

Task:
[Agent-specific task description from above]

Requirements:
- Output must be valid JSON matching schemas/stage-0b.json
- "findings" must be human-readable and specific to this transcript
- "recommendations" must be actionable and include rationale (minimum 10 characters per rationale)
- Focus on signals that drive the Stage 0c synthesis decision
- Be specific about patterns, counts, and observed characteristics

Return ONLY the JSON object, no additional text.
```

### Parallel Execution

All four agents run concurrently. The workflow:
1. Load `raw_signals.json`
2. Invoke timestamp, speaker, density, and format agents in parallel
3. Collect all four outputs
4. Pass to Stage 0c synthesis agent

There is no inter-agent dependency. Each agent is independent and self-contained.

---

## Output Validation

Each agent output must conform to `schemas/stage-0b.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Stage 0b Analysis Agent Output",
  "type": "object",
  "properties": {
    "agent_type": {
      "type": "string",
      "enum": ["timestamp", "speaker", "density", "format"]
    },
    "findings": {
      "type": "string",
      "minLength": 1
    },
    "recommendations": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "properties": {
          "recommendation": { "type": "string", "minLength": 1 },
          "rationale": { "type": "string", "minLength": 10 }
        },
        "required": ["recommendation", "rationale"],
        "additionalProperties": false
      }
    }
  },
  "required": ["agent_type", "findings", "recommendations"],
  "additionalProperties": false
}
```

All four agent outputs must be valid against this schema.

---

## Key Design Decisions

1. **Parallel Execution**: Agents are independent to enable concurrent processing and faster analysis.

2. **Shared Input**: All agents read the same `raw_signals.json`, but each focuses on different signals.

3. **Recommendations Format**: Both the `recommendation` and `rationale` fields are required. The rationale must be at least 10 characters (enforced by schema) to ensure substantive justification.

4. **No Cross-Agent Communication**: Agents do not read each other's outputs during Stage 0b. Synthesis happens in Stage 0c.

5. **Signal Confidence**: Agents use the signal quality indicators in `raw_signals.json` to calibrate confidence in recommendations (e.g., if coverage is 50%, recommendations might be more tentative).
