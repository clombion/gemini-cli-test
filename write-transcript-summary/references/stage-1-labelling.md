# Stage 1: Labelling

## Overview

Stage 1 transforms chunks into labelled records by filling interpretive fields. It consists of three sub-stages:

- **Stage 1a (Labelling)**: Deterministic chunking, scaffold generation, and LLM labelling with schema validation
- **Stage 1b (Optional Self-Consistency)**: Independent re-labelling pass to flag divergent chunks
- **Stage 1c (Count Gate)**: Mechanical verification that all chunks have complete label records

This stage is LLM-heavy (Stage 1a) with optional quality checking (Stage 1b) and deterministic gating (Stage 1c).

---

## Stage 1a: Chunking, Scaffolding, and Labelling

### Deterministic Chunking

Chunking strategy is determined by Stage 0c synthesis. Three strategies are implemented in `scripts/chunk_transcript.py`:

#### Strategy 1: Timestamp-Accumulation
- Accumulates word count until reaching `accumulated_ms` time threshold
- Uses timestamps from the transcript to define chunk boundaries
- Best for transcripts with regular, reliable timestamps
- Respects timestamp-aligned splits before forcing word-count split
- Overlap: carries forward `overlap_words` from previous chunk (default 150)

#### Strategy 2: Turn-Accumulation
- Accumulates word count until reaching `chunk_size_words` or a speaker turn boundary
- Prioritizes speaker transitions as soft chunk boundaries
- Best for multi-speaker transcripts where speaker change indicates topic/function shift
- Falls back to word-count limit if no speaker transition occurs within window
- Overlap: carries forward `overlap_words` (default 150)

#### Strategy 3: Word-Count-Fallback
- Accumulates words until reaching `chunk_size_words` (e.g., 300 words)
- Most deterministic; no timestamp or speaker dependency
- Used when timestamps/speakers are absent or unreliable
- Overlap: carries forward `overlap_words` (default 150)

#### Boundary Enforcement
All strategies share a critical rule: **never split mid-utterance**. If a chunk boundary falls within a speaker's turn, defer the split until that speaker finishes (end of turn marker or new speaker label).

### Scaffold Generation

Before LLM labelling, each chunk is wrapped in a **scaffold**: a JSON object with deterministic and interpretive fields.

#### Deterministic Fields (Populated by Chunking Script)
- `chunk_index` (integer): Sequential identifier, 0-indexed
- `word_count` (integer): Total word count of the chunk
- `word_range` (object): `{start, end}` indices in the original transcript
- `timestamp_range` (object): `{start, end}` timestamps (if available); may be omitted
- `speaker_tags` (array of strings): List of identified speakers; may be omitted if no speakers
- `raw_source_text` (string): Original text of the chunk, character-for-character (no paraphrasing)

#### Interpretive Fields (Null, for LLM to Fill)
- `topic_tags` (array): 1-5 alphanumeric tags (min 3 chars each) identifying topics
- `topic_tags_rationale` (string): Why these tags; min 20 chars
- `key_entities` (array): Named entities, persons, places, concepts mentioned
- `entities_rationale` (string): Why these entities; min 20 chars
- `conversational_function` (enum): One of `decision`, `question`, `elaboration`, `digression`
- `function_rationale` (string): Why this function; min 20 chars
- `speaker_intent` (string): What is the speaker(s) trying to accomplish in this chunk?
- `intent_rationale` (string): Why this intent; min 20 chars

**Raw source text is the single source of truth.** The LLM must ground all interpretive fields in the actual chunk text. No paraphrasing or inference beyond the chunk is permitted.

### LLM Labelling Agent

The labelling agent reads one chunk scaffold and fills all interpretive fields.

#### Agent Prompt Structure

```
You are a transcript labelling agent.

Your role: Read the chunk scaffold below and fill interpretive fields.

Chunk Scaffold:
[JSON with deterministic fields + null interpretive fields]

Task:
1. Assign 1-5 topic tags (alphanumeric + underscore, min 3 chars each).
   Rationale: Explain which parts of the chunk drove each tag assignment. Min 20 chars.

2. Extract key entities: named entities, people, places, concepts, products.
   Rationale: Explain why these are key. Min 20 chars.

3. Assign conversational function: decision, question, elaboration, or digression.
   Rationale: Explain the speaker's communicative intent. Min 20 chars.

4. Assign speaker intent: What is the speaker(s) trying to accomplish?
   Rationale: Explain based on function, content, and context. Min 20 chars.

Requirements:
- All rationales must cite specific text from the chunk.
- Do not paraphrase raw_source_text; output the exact text as-is.
- Return valid JSON matching schemas/stage-1a.json.
- All required fields must be filled; no nulls allowed.
```

#### Vocabulary

Conversational functions are fixed:
- `decision`: Speaker(s) reaching or announcing a choice or commitment
- `question`: Speaker(s) asking for information or clarification
- `elaboration`: Speaker(s) expanding on prior point with detail
- `digression`: Speaker(s) moving to an unrelated topic

#### Constraints

1. **Topic Tags**:
   - Alphanumeric + underscore only (regex: `^[a-zA-Z0-9_]+$`)
   - Minimum 3 characters per tag
   - Maximum 5 tags total
   - At least 1 tag required

2. **Key Entities**:
   - Extract actual named entities from the chunk
   - Include people, places, organizations, products, concepts
   - At least 1 entity required
   - No paraphrasing; use exact names/terms

3. **Conversational Function**:
   - Exactly one value from enum
   - Must be grounded in what the speaker is doing (asking, deciding, elaborating, sidetracking)

4. **Speaker Intent**:
   - Always required (every chunk has a speaker with intent, named or not)
   - Min 1 character; no max
   - Describe the goal or purpose (e.g., "clarify budget implications", "gain consensus", "set timeline")

5. **All Rationales**:
   - Minimum 20 characters each
   - Must cite specific phrases or patterns from `raw_source_text`
   - Substantive, not generic (e.g., not "because the tag applies")

### Schema Validation

After each batch of labelled chunks, run schema validation:

```bash
scripts/validate_schema.py --stage 1a --input batch_labels.jsonl --config config.json
```

#### Output Schema

Each labelled chunk must conform to `schemas/stage-1a.json`:

```json
{
  "chunk_index": 0,
  "word_range": {"start": 0, "end": 250},
  "timestamp_range": {"start": "0:00", "end": "1:15"},
  "speaker_tags": ["Alice", "Bob"],
  "word_count": 245,
  "raw_source_text": "Alice: ...",
  "topic_tags": ["budget_planning", "resource_allocation"],
  "topic_tags_rationale": "...",
  "key_entities": ["Q3 budget", "engineering team", "$500K"],
  "entities_rationale": "...",
  "conversational_function": "decision",
  "function_rationale": "...",
  "speaker_intent": "Reach agreement on budget allocation for Q3",
  "intent_rationale": "..."
}
```

#### Validation Outcomes

**Hard Errors** (exit code 65, pipeline halts):
- Empty rationale field (< 1 character)
- Topic tag violates regex or length constraints
- Conversational function not in enum
- Schema violation (missing required field, wrong type)
- Raw source text empty or null

**Warnings** (exit code 0, pipeline continues but flags for review):
1. **Rationale Length Warning**: If any rationale < 20 characters
2. **Fatigue Pattern**: If second half of batch has mean rationale length < 70% of first half
   - Suggests LLM shortcutting under volume
   - Recommendation: human should review the batch before proceeding
3. **Dominant-Value Pattern**: If any conversational function >50% of batch
   - May be legitimate (e.g., all-question Q&A session)
   - Unusual enough to flag for confirmation

#### Processing Warnings

**Fatigue Warning**:
- Calculate mean rationale length for first 50% of chunk batch
- Calculate mean rationale length for second 50%
- If `mean_second_half < 0.70 * mean_first_half`, flag batch
- **Action**: Human should spot-check second half to ensure quality hasn't degraded

**Dominant-Value Warning**:
- Count occurrences of each conversational function
- If any function > 50% of chunk count, flag
- **Action**: Human should confirm this reflects actual transcript pattern (e.g., a series of questions is legitimate in a Q&A session)

### Handling Validation Warnings

Warnings do not block the pipeline but should be surfaced to the human operator before Stage 1b/1c proceeds.

1. **Fatigue Pattern Detected**:
   - Recommend: Review the latter half of the batch manually
   - Consider: Re-run labelling on the second half with fresh API calls
   - Re-validate; if still warned, human can override and proceed

2. **Dominant-Value Pattern Detected**:
   - Recommend: Confirm the pattern is legitimate (e.g., question-heavy Q&A is expected)
   - If unexpected, consider: Re-run labelling with adjusted prompt to encourage diversity
   - Human can override and proceed if appropriate

---

## Stage 1b: Optional Self-Consistency Pass

### Overview

Self-consistency is an optional quality-assurance layer that re-labels chunks independently to detect inconsistency.

**Enabled via**: `config.self_consistency_enabled = true` (default: `false`)

### How It Works

1. **Independent Re-Labelling**: Run the labelling agent again on the same chunks with a fresh API call
2. **Second Pass Records**: Produces a second set of label records (separate JSON file)
3. **Deterministic Comparison**: Compare topic_tags between first and second pass
4. **Divergence Detection**: Chunks with different topic_tags are flagged as divergent
5. **Manifest Update**: Add divergent chunk IDs to manifest for Stage 2 review
6. **Preservation**: Both passes are retained; first pass feeds downstream, second pass for reference

### Output Format

Self-consistency produces a parallel set of label records:
```
labels_pass1.jsonl    (used downstream; stage 1c validates both)
labels_pass2.jsonl    (for review; may contain divergences)
self_consistency_report.json
{
  "total_chunks": 42,
  "divergent_chunks": [3, 7, 15],
  "divergence_rate": 0.071,
  "divergent_topic_tags": {
    "3": {
      "pass1": ["budget_discussion", "allocation"],
      "pass2": ["financial_planning", "resource_mgmt"]
    },
    ...
  }
}
```

### Cost Trade-off

- **Doubles Stage 1a API cost** (two independent passes)
- **Recommended for**: High-stakes transcripts (strategic decisions, legal proceedings, confidential meetings)
- **Not recommended for**: Low-risk analysis, exploratory summaries, high-volume processing

### Divergent Chunk Flagging

Chunks with divergent topic_tags are marked in the manifest:
```json
{
  "chunk_id": 3,
  "divergent": true,
  "reason": "Self-consistency divergence in topic_tags"
}
```

These are surfaced at Stage 2c human gate for explicit review and approval.

---

## Stage 1c: Count Gate

### Purpose

Mechanical gate verifying all chunks have complete label records before proceeding to Stage 2.

### Process

1. **Load Manifest**: Read expected chunk count from config/manifest
2. **Validate Stage 1a**: Count complete label records in `labels_pass1.jsonl`
3. **Validate Stage 1b** (if enabled): Count complete label records in `labels_pass2.jsonl`
4. **Count Match**: Both counts must equal manifest expected count
5. **Exit Condition**:
   - Success (exit 0): Counts match, proceed to Stage 2
   - Error (exit 65): Counts don't match, pipeline halts

### Error Handling

If count doesn't match:
```
ERROR: Label record count mismatch
  Expected: 42 chunks
  Pass 1: 41 records
  Pass 2: 41 records (if self_consistency_enabled)

Missing chunk IDs: [39]

Action: Re-label missing chunks and revalidate.
```

### Schema Validation During Gate

The count gate also runs minimal schema validation on sampled records (e.g., first, middle, last) to catch systematic issues. If schema violations detected, exit 65.

---

## Quality Checklist for Stage 1

- [ ] All chunks deterministically generated using configured strategy (timestamp/turn/word-count)
- [ ] Boundaries respect utterance integrity (no mid-speaker splits)
- [ ] Scaffold generation includes all deterministic fields with correct types
- [ ] LLM labelling produces interpretive fields for all chunks
- [ ] Topic tags conform to alphanumeric + underscore pattern, min 3 chars, 1-5 per chunk
- [ ] All rationales >= 20 characters with specific textual citations
- [ ] Schema validation passes (no hard errors)
- [ ] Validation warnings reviewed and addressed (fatigue/dominant-value)
- [ ] If self-consistency enabled: both passes complete, divergences recorded
- [ ] Count gate passes: label record count matches manifest
- [ ] Proceed to Stage 2

---

## Example: Chunk Labelling Workflow

### Input Chunk Scaffold

```json
{
  "chunk_index": 5,
  "word_range": {"start": 1200, "end": 1450},
  "timestamp_range": {"start": "5:30", "end": "6:45"},
  "speaker_tags": ["Alice", "Bob"],
  "word_count": 245,
  "raw_source_text": "Alice: We need to finalize the Q3 budget. Bob: I have figures for engineering and marketing. Alice: How much for each? Bob: Engineering is $500K, marketing is $200K. Alice: That's within our plan. Let's move forward.",
  "topic_tags": null,
  "topic_tags_rationale": null,
  "key_entities": null,
  "entities_rationale": null,
  "conversational_function": null,
  "function_rationale": null,
  "speaker_intent": null,
  "intent_rationale": null
}
```

### LLM Output

```json
{
  "chunk_index": 5,
  "word_range": {"start": 1200, "end": 1450},
  "timestamp_range": {"start": "5:30", "end": "6:45"},
  "speaker_tags": ["Alice", "Bob"],
  "word_count": 245,
  "raw_source_text": "Alice: We need to finalize the Q3 budget. Bob: I have figures for engineering and marketing. Alice: How much for each? Bob: Engineering is $500K, marketing is $200K. Alice: That's within our plan. Let's move forward.",
  "topic_tags": ["budget_finalization", "q3_planning", "resource_allocation"],
  "topic_tags_rationale": "Alice explicitly states 'We need to finalize the Q3 budget' triggering budget_finalization tag. Discussion of departmental allocations ($500K engineering, $200K marketing) justify q3_planning and resource_allocation.",
  "key_entities": ["Q3 budget", "engineering", "marketing", "$500K", "$200K"],
  "entities_rationale": "Budget amounts and departments are central to the decision being made. These are the concrete values and units over which the decision is made.",
  "conversational_function": "decision",
  "function_rationale": "Alice and Bob reach and announce agreement: 'That's within our plan. Let's move forward.' This is a commitment to a specific budget allocation.",
  "speaker_intent": "Alice seeks budget confirmation; Bob provides numbers; Alice confirms approval and commits to moving forward",
  "intent_rationale": "Alice initiates by asking Bob for figures (seeking confirmation), Bob provides specifics (fulfilling request), Alice confirms acceptance and commits (decision closure)."
}
```

---

## References

- `schemas/stage-1a.json` — Chunk label record schema
- `scripts/chunk_transcript.py` — Chunking implementation (timestamp/turn/word-count)
- `scripts/validate_schema.py` — Validation and warning detection
- `vocab.json` — Conversational function enum and other vocabularies
