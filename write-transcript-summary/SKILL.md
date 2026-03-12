---
name: write-transcript-summary
description: Create structured, faithful summaries from transcripts and recorded media with full source grounding and audit trails. Handles meetings, interviews, podcasts, panel discussions, webinars, legal proceedings, keynotes, presentations, and recorded conversations. Use this skill whenever you have a transcript file or raw recording (audio/video) as the primary input and the user wants a summary — whether they ask to "summarise", "create a summary", "get key insights", "extract takeaways", "understand the main themes", or "summarize the keynote". Automatically handles all input formats: transcribed meeting notes, interview transcripts, podcast transcripts, webinar recordings, recorded keynotes, video presentations, phone calls, legal proceedings, and panel discussions. Supports all three chunking strategies automatically: timestamp-based (meetings and recorded media with temporal markers), turn-based (interviews, conversations, and panel discussions), and word-count (monologues, keynotes, and presentations). Always produces summaries with full topic structure, source grounding, and a manifest audit trail proving every claim is traceable to the original source material.
---

# write-transcript-summary Skill

A multi-stage LLM harness that produces faithful, auditable summaries from raw transcript files. The pipeline enforces deterministic scaffolding before LLM interpretation, human review gates at critical junctures, and fan-out/fan-in orchestration with specialised agents.

## Pipeline Philosophy

Every stage of this pipeline honours six invariants:

1. **Source text travels verbatim** — LLM never paraphrases source material; all quotations, references, and spans are direct citations from the transcript.

2. **LLM fills only interpretive fields** — the deterministic scaffold owns all structural decisions; LLM acts as a Translator of Intent, not a Manager of State. Scaffold-generated fields (chunk boundaries, chunk IDs, span anchors) are immutable.

3. **Every interpretive field must have a paired `_rationale` field** — reviewers need the reasoning behind each assignment, not just the final value. Rationale quality is validated: minimum 20 characters, no fatigue detection, no dominant-value shortcuts.

4. **Human gates before any stage whose errors propagate downstream** — three explicit gates (stages 0d, 2c, 4g) pause the pipeline and display artifacts for review before proceeding.

5. **Incoherence is surfaced explicitly** — never silently propagated. Stage 0c performs four coherence checks; flagged issues block stage 0d until approved or corrected.

6. **Every stage appends to the pipeline manifest** — `manifest.jsonl` is the proof of provenance. Downstream stages check manifest entries, not just file existence. No manifest entry = stage not complete.

## Quick Start

Provide a transcript file (plain text, with or without timestamps and speaker labels). The skill orchestrates 15 stages automatically:

- **Stages 0a–0d**: Analysis phase. Deterministic pre-scan, four parallel analysis agents, synthesis of configuration, human approval.
- **Stages 1a–1c**: Chunking and labelling phase. Deterministic chunking per approved strategy, LLM labelling with rationale, count validation.
- **Stages 2a–2c**: Topic inference phase. Topic merging, question generation, human approval.
- **Stages 3–3v**: Extraction phase. Per-topic QA extraction, coverage audit.
- **Stages 4–4g**: Rewriting and delivery phase. Rewriting synthesis, QA grounding loop, human final approval.

The final output is `output/summary.md` — a faithful synthesis organized by topic with verbatim extracts and clear lineage back to source.

## Workspace Layout

The skill creates a workspace alongside your transcript file: `<transcript-dir>/<transcript-name>-workspace/`

```
<name>-workspace/
├── manifest.jsonl            ← Pipeline provenance log (append-only)
├── raw_signals.json          ← Stage 0a: deterministic transcript properties
├── analysis/                 ← Stage 0b: agent output files
├── config.json               ← Stage 0c–0d: approved configuration
├── chunks/                   ← Stage 1a: scaffold chunk records
├── labels/                   ← Stage 1a: LLM-filled label records
├── topics/                   ← Stage 2a–2b: topic map and questions
├── extracts/                 ← Stage 3: per-question answer records
└── output/                   ← Stage 4: final summary and audit trail
    └── summary.md            ← Final deliverable
```

All artefacts are JSON (or JSONL for manifest) except the final Markdown output. Every JSON file enforces `additionalProperties: false` and controlled vocabulary validation.

## Process Map

```
┌─────────────┐
│ Transcript  │
│    File     │
└──────┬──────┘
       │
       │ pre_scan.py (deterministic)
       ▼
    ┌──────────────────┐
    │  raw_signals.json│
    └────────┬─────────┘
             │
             │ 4× analysis agents (fan-out)
             │ timestamp, speaker, density, format
             ▼
    ┌──────────────────────┐
    │ analysis/*.json      │
    └────────┬─────────────┘
             │
             │ synthesis agent + coherence checks
             ▼
    ┌──────────────────┐
    │ config.json      │
    └────────┬─────────┘
             │
             ├─► [HUMAN GATE 0d: Approve config]
             │
             │ chunk_transcript.py (deterministic)
             ▼
    ┌──────────────────────┐
    │ chunks/*.json        │
    │ (scaffold only)      │
    └────────┬─────────────┘
             │
             │ label agents (fan-out)
             │ topic_tags, entities, intent, function
             ▼
    ┌──────────────────────┐
    │ labels/*.json        │
    │ (scaffold + filled)  │
    └────────┬─────────────┘
             │
             │ [optional: self-consistency pass 1b]
             │ merge agent
             ▼
    ┌──────────────────────────┐
    │ topics/topic_map.json    │
    │ topics/questions.json    │
    └────────┬─────────────────┘
             │
             ├─► [HUMAN GATE 2c: Approve topics + questions]
             │
             │ extract agents (fan-out)
             │ per-topic QA extraction
             ▼
    ┌──────────────────────┐
    │ extracts/*.json      │
    │ coverage audit       │
    └────────┬─────────────┘
             │
             │ rewrite agent
             │ QA grounding loop
             ▼
    ┌──────────────────────┐
    │ output/summary.md    │
    │ (unfilled scaffold)  │
    └────────┬─────────────┘
             │
             ├─► [HUMAN GATE 4g: Approve final output]
             │
             ▼
    ┌──────────────────────┐
    │  Deliver Summary     │
    └──────────────────────┘
```

**Responsibility table:**

| Stage | Operation | Type | Constraint |
|-------|-----------|------|-----------|
| 0a | Format, encoding, timestamp/speaker detection | Deterministic | Stdlib only |
| 0b | Timestamp, speaker, density, format analysis | LLM (4 agents) | Schema: `stage-0b.json`; rationale required |
| 0c | Synthesis of config from analysis; coherence checks | LLM + Deterministic | 4 validation checks; incoherence flagged explicitly |
| 0d | Review and approval of config | **Human Gate** | Manifest marks approval; 1c gates downstream |
| 1a | Chunking per strategy; labelling scaffold | Deterministic + LLM | 3 strategies; scaffold enforces structure; rationale required for topic_tags, entities, intent, function |
| 1b | Optional second-pass labelling for self-consistency | LLM | Off by default; enabled via config |
| 1c | Verify every chunk labeled; count gate | Deterministic | Manifest prerequisite check for 0d |
| 2a | Topic merging; divergent chunk handling | LLM | Schema: `stage-2a.json`; handles 1b divergence |
| 2b | Generate questions per topic | LLM | Schema: `stage-2b.json` |
| 2c | Review and approval of topic map + questions | **Human Gate** | Manifest marks approval; gates stage 3 |
| 3 | Per-topic QA extraction (fan-out) | LLM | Verbatim spans only; answer_rationale required; no paraphrase |
| 3v | Coverage audit; unanswered-question protocol | Deterministic | Every chunk in ≥1 extract; every question addressed or flagged |
| 4 | Rewriting synthesis per topic | LLM | Scaffold with pre-populated headers; extracts as source; faithfulness constraint |
| 4v | QA grounding loop; claim traceability check | Deterministic | Every claim in output traceable to pre-approved extract |
| 4g | Review and approval of final output | **Human Gate** | Manifest audit trail; delivery |

## Stage Index

Each stage links to its reference file. Before proceeding with any stage, read the corresponding reference file for prompts, schemas, and detailed validation rules.

### Phase 0: Analysis & Configuration

**0a: Pre-scan**
- Deterministic extraction of transcript properties: format, encoding, character/line count, timestamp presence and format, speaker label presence, segment count estimate.
- Output: `raw_signals.json`
- Action: Run `scripts/pre_scan.py`
- Reference: *Inline in script docstring*

**0b: Parallel analysis agents**
- Four independent LLM agents read `raw_signals.json` and produce analysis outputs: timestamp agent, speaker agent, density agent, format agent.
- Output: `analysis/timestamp.json`, `analysis/speaker.json`, `analysis/density.json`, `analysis/format.json`
- Schema: `schemas/stage-0b.json` (one schema for all four agents; agent field disambiguates)
- Reference: `references/stage-0b-agents.md`

**0c: Config synthesis & coherence validation**
- LLM synthesis agent reads all four analysis outputs and produces `config.json` (chunking strategy, chunk size, overlap, `speakers_identified` flag, self-consistency flag).
- Deterministic coherence checks validate config against analysis for logical consistency.
- Output: `config.json` (if coherent); incoherence flags in manifest
- Reference: `references/stage-0cd-synthesis-gate.md`

**0d: Human gate — approve config**
- Display `config.json`; user reviews and replies "approved" or with corrections.
- Skill marks approval in manifest; downstream stages (e.g., 1c) check manifest before proceeding.
- Prerequisite for: Stage 1a
- Reference: `references/stage-0cd-synthesis-gate.md`

### Phase 1: Chunking & Labelling

**1a: Deterministic chunking + LLM labelling**
- `scripts/chunk_transcript.py` reads transcript and `config.json`; produces deterministic chunk scaffold.
- LLM labelling agents fill: `topic_tags`, `topic_tags_rationale`, `key_entities`, `entities_rationale`, `speaker_intent`, `intent_rationale`, `conversational_function`, `function_rationale`.
- Output: `chunks/*.json` (scaffold); `labels/*.json` (filled)
- Schema: `schemas/stage-1a.json`
- Manifest prerequisite: Stage 0d must be approved
- Reference: `references/stage-1-labelling.md`

**1b: Optional self-consistency pass**
- If `self_consistency_enabled: true` in config, run a second independent labelling pass and deterministic comparison.
- Flag divergent chunks (where two passes disagree) for human review before proceeding.
- Output: `divergent_chunks.json` (if any)
- Schema: `schemas/stage-1a.json`
- Reference: `references/stage-1-labelling.md`

**1c: Count gate**
- Verify every chunk has a label record.
- Manifest prerequisite check: Stage 0d must be approved.
- Run `scripts/validate_schema.py` on all label records; surface rationale quality warnings (minimum length, fatigue, dominant-value pattern).
- If warnings present: display to human, flag in manifest, continue only if approved by human.
- Output: manifest entry if passed
- Reference: `references/stage-1-labelling.md`

### Phase 2: Topics & Questions

**2a: Topic merging**
- LLM merging agent reads all label records and produces topic map: one entry per inferred topic, with chunk membership list.
- Handles divergent chunks from 1b if present.
- Output: `topics/topic_map.json`
- Schema: `schemas/stage-2a.json`
- Reference: `references/stage-2-topics.md`

**2b: Question generation**
- LLM question agent reads topic map and produces per-topic question sets.
- Output: `topics/questions.json`
- Schema: `schemas/stage-2b.json`
- Reference: `references/stage-2-topics.md`

**2c: Human gate — approve topic map + questions**
- Display topic map and questions together; user reviews and replies "approved" or with corrections.
- Coverage gate (deterministic): verify every chunk appears in at least one topic.
- Skill marks approval in manifest; downstream stages check manifest before proceeding.
- Prerequisite for: Stage 3
- Reference: `references/stage-2-topics.md`

### Phase 3: Extraction

**3: Per-topic QA extraction**
- Fan-out: one LLM extract agent per topic.
- Agent reads topic name, chunk IDs, verbatim source, pre-generated questions; produces answers: verbatim spans with chunk index, anchor, and rationale.
- No paraphrase rule enforced; schema validation rejects derived claims.
- Output: `extracts/topic-<ID>.json` (one per topic)
- Schema: `schemas/stage-3.json`
- Manifest prerequisite: Stage 2c must be approved
- Reference: `references/stage-3-extraction.md`

**3v: Coverage audit**
- Deterministic validation: every chunk must appear in at least one extract.
- Every pre-generated question must be addressed (answered or explicitly unanswered with rationale).
- Run `scripts/validate_schema.py` on all extract records.
- Output: coverage report in manifest
- Reference: `references/stage-3-extraction.md`

### Phase 4: Rewriting & Delivery

**4: Rewriting synthesis**
- LLM rewrite agent reads topic map (headers pre-populated), extracts (with verbatim source text), questions; produces narrative synthesis per topic.
- Faithfulness constraint: "Introduce no claim not present in the provided extracts."
- Output: `output/summary.md` (unfilled scaffold at this stage)
- Schema: Markdown structure; no formal JSON
- Reference: `references/stage-4-rewriting.md`

**4v: QA grounding loop**
- Deterministic validation: run separate pass against pre-approved questions and extracts.
- Check every claim in output for traceability to an extract; flag unsourced claims.
- Output: traceability report in manifest
- Reference: `references/stage-4-rewriting.md`

**4g: Human gate — approve final output**
- Display `output/summary.md`; user reviews and replies "approved" or requests revisions.
- Skill marks approval in manifest; pipeline complete.
- Prerequisite for: Delivery
- Reference: `references/stage-4-rewriting.md`

## Running the Skill

1. **Provide a transcript file** — point to a plain-text file (with or without timestamps and speaker labels).

2. **Skill asks for path if needed** — if not provided in initial message, skill prompts: "What's the transcript file?"

3. **Skill creates workspace** — automatically creates `<transcript-dir>/<transcript-name>-workspace/` alongside the transcript.

4. **Orchestrate stages 0a–4g** — skill runs each stage in sequence, calling scripts and LLM agents as needed.

5. **Pause at human gates** — at stages 0d, 2c, 4g, skill displays artifact inline and pauses for your approval or corrections.

6. **Your reply resumes pipeline** — reply "approved" to proceed, or provide corrected JSON to resume with changes.

7. **Final output** — `output/summary.md` is your summary; workspace contains full audit trail in `manifest.jsonl`.

## At Each Stage: Read the Reference File

Before proceeding, consult the reference file for that stage. It contains the LLM agent prompts, exact output schemas, validation rules, and troubleshooting guidance.

| Stage | Reference File |
|-------|---|
| 0a | *Inline in pre_scan.py docstring* |
| 0b | `references/stage-0b-agents.md` |
| 0c, 0d | `references/stage-0cd-synthesis-gate.md` |
| 1a, 1b, 1c | `references/stage-1-labelling.md` |
| 2a, 2b, 2c | `references/stage-2-topics.md` |
| 3, 3v | `references/stage-3-extraction.md` |
| 4, 4v, 4g | `references/stage-4-rewriting.md` |

## Key Design Decisions

**Rationale fields required for all LLM interpretations** — every `topic_tags`, `key_entities`, `speaker_intent`, `conversational_function`, `topic_name`, `questions`, `answers`, and rewritten prose must include a `_rationale` field explaining the LLM's reasoning. This surfaces potential hallucinations and makes human review tractable.

**Manifest-based prerequisite checking** — downstream stages read `manifest.jsonl` and check for predecessor stage entries, not just file existence. A script exiting cleanly without failure does not guarantee successful execution; the manifest is the proof.

**Three warning-level checks in 1c** — after labelling, `validate_schema.py` flags:
- **Minimum meaningful length** — rationale fields shorter than 20 characters (catches low-effort responses like "relevant.")
- **Fatigue pattern** — if mean rationale length in the second half of a batch is <70% of the first half, LLM is shortcutting under volume
- **Dominant-value pattern** — if any single `conversational_function` value covers >50% of records, the transcript likely isn't truly conversational or the LLM is overusing a shortcut

These warnings do not block the pipeline but are surfaced to you for review before proceeding.

**Workspace alongside transcript** — each transcript run gets its own workspace (`<transcript-dir>/<name>-workspace/`), making it easy to find and clean up later. No global state; no shared temporary directories.

**Inline display + reply for human gates** — when the pipeline reaches a human gate (0d, 2c, 4g), the artifact is displayed inline (as formatted JSON or Markdown) and you reply in the same session with "approved" or corrections. This keeps the full context and flow visible without context-switching.

**Three human gates, not more** — gates are placed before error-propagating stages (config synthesis, topic merging, final output). Earlier stages (pre-scan, labelling, extraction) are gated only by schema validation and manifest prerequisites, not human review, to keep turnaround fast for high-confidence stages.

## Scripts

### `scripts/pre_scan.py`

Deterministic, stdlib-only extraction of transcript properties.

```bash
python scripts/pre_scan.py TRANSCRIPT WORKSPACE [--force]
```

- Reads transcript file; outputs `raw_signals.json` with format, encoding, character count, line count, timestamp/speaker presence, segment count estimate.
- No LLM required.
- Appends entry to `manifest.jsonl`.
- Idempotent: exits code 5 if output exists (use `--force` to overwrite).

### `scripts/chunk_transcript.py`

Deterministic chunking per configured strategy (timestamp-accumulation, turn-accumulation, word-count fallback).

```bash
python scripts/chunk_transcript.py TRANSCRIPT WORKSPACE [--force]
```

- Reads transcript and `config.json` from workspace.
- Produces scaffold chunk records in `chunks/` with pre-populated deterministic fields.
- Enforces boundary constraints (no mid-utterance splits); handles configurable overlap.
- Appends entry to `manifest.jsonl`.
- Manifest prerequisite: Stage 0d (human approval) must be in manifest before running.
- Idempotent: exits code 5 if chunks exist (use `--force` to regenerate).

### `scripts/validate_schema.py`

Validates LLM-filled JSON against schema, controlled vocabulary, and rationale quality.

```bash
python scripts/validate_schema.py WORKSPACE --stage STAGE_ID [--file FILE]
```

- Reads JSON file(s) for a given stage; validates against `schemas/stage-<ID>.json` and `vocab.json`.
- Hard errors (exit 65, block pipeline): schema violations, vocab violations, empty rationale, modified scaffold fields.
- Warnings (exit 0, surfaced but do not block): short rationale (<20 chars), fatigue pattern, dominant-value pattern.
- Output: JSON object with `errors` (array) and `warnings` (array) on stdout.
- Called after each LLM fill pass.

### `scripts/status.py`

Reports pipeline status and next action.

```bash
python scripts/status.py WORKSPACE [--json] [--quiet]
```

- **Human mode (default):** Rich-formatted table showing each stage (✓ / pending / blocked / ✗), highlighted next action, any validation failures with fix hints.
- **LLM mode** (`--json`): JSON to stdout with stage statuses, next action command, blocked gates, failures.
- Reads `manifest.jsonl` and workspace artifact directories.
- No interactive mode; status is read-only.
- Respects `NO_COLOR` env var in human mode.

## Controlled Vocabulary

Defined in `vocab.json`; schema validation rejects unknown terms on write.

**`conversational_function`** — LLM assigns to each chunk:
- `decision` — speaker makes or commits to a choice
- `question` — speaker seeks information or clarification
- `elaboration` — speaker explains, develops, or provides evidence
- `digression` — speaker deviates from core topic

**`timestamp_strategy`** — assigned by stage 0c (synthesis) based on analysis:
- `timestamp-accumulation` — per-utterance or fixed-interval timestamps; group by time
- `turn-accumulation` — speaker turns without timestamps; group by turn count
- `word-count-fallback` — no timestamps or turns; group by word count

**`speaker_type`** — assigned by stage 0b (speaker analysis):
- `named` — speakers identified by name or clear role
- `anonymous` — speakers labelled generically ("Speaker 1", "Participant A")
- `absent` — no speaker labels (monologue or transcript format without speaker lines)

## Human Gates

### Gate 0d: Config Approval

Skill displays `config.json` in formatted JSON. You review and reply:
- `approved` — pipeline continues to stage 1a
- JSON with edits — skill updates config and marks approved in manifest

Approval is recorded in manifest; stage 1c checks for this entry before proceeding.

### Gate 2c: Topic Map & Questions Approval

Skill displays `topics/topic_map.json` and `topics/questions.json` together. You review and reply:
- `approved` — pipeline continues to stage 3
- JSON with edits — skill updates both files and marks approved in manifest

Skill also runs deterministic coverage gate: every chunk must appear in at least one topic. If coverage fails, skill flags it and pauses.

### Gate 4g: Final Output Approval

Skill displays `output/summary.md`. You review and reply:
- `approved` — pipeline marks complete in manifest; summary is ready
- Markdown with edits — skill updates summary.md and marks approved in manifest

Before approval, stage 4v runs QA grounding loop: every claim checked for traceability to pre-approved extracts. Unsourced claims are flagged inline in the output.

## Configuration (config.json)

Approved at stage 0d. Controls all downstream chunking and labelling.

```json
{
  "chunking_strategy": "timestamp-accumulation|turn-accumulation|word-count-fallback",
  "chunk_size_words": 300,
  "overlap_words": 150,
  "speakers_identified": true|false,
  "self_consistency_enabled": false
}
```

- `chunking_strategy` — determined by stage 0c based on stage 0b analysis; immutable after 0d approval.
- `chunk_size_words` — typical 300; adjust for longer chunks if topics span many short utterances.
- `overlap_words` — typical 150; provides context continuity between chunks.
- `speakers_identified` — true if stage 0b analysis found named or consistent speaker labels.
- `self_consistency_enabled` — false by default; set true for high-stakes transcripts (governance meetings, legal proceedings, interviews) to enable stage 1b second-pass verification.

## References to ai-llm-harness

This skill implements patterns from `ai-llm-harness/references/pipeline.md`:

- **Fan-out/fan-in orchestration** — stages 0b, 1a, 3 spawn multiple agents in parallel; stages 0c, 2a, 4 merge results.
- **Count gates** — stage 1c verifies every chunk labelled; stage 3v verifies every chunk extracted; stage 4v verifies every claim grounded.
- **Scaffold pattern** — deterministic structure pre-populated; LLM fills only interpretation; scaffold fields immutable.
- **Manifest-based state machine** — each stage appends to manifest; downstream checks manifest for prerequisites, not file existence.

See `/Users/datum/.claude/skills/ai-llm-harness/references/pipeline.md` for implementation patterns.

## Quality Assurance

The pipeline enforces faithfulness through:

1. **Verbatim source** — all spans are direct citations; no paraphrasing.
2. **Rationale transparency** — every interpretation documented with reasoning.
3. **Explicit human review** — three gates review configuration, topics, and final output.
4. **Coverage verification** — deterministic audit that every chunk is extracted and every extract is grounded in final output.
5. **Schema + vocabulary enforcement** — all JSON validates at write time; controlled vocabulary prevents category drift.

If a stage fails validation, the skill halts and surfaces the error with a fix suggestion. No silent propagation of bad data.

## Exit Codes

All scripts use standard exit codes:

| Code | Meaning | Retriable? |
|------|---------|-----------|
| 0 | Success | — |
| 2 | Usage error (bad args) | No |
| 5 | Conflict (output exists, use `--force`) | No |
| 65 | Data error (malformed transcript/JSON) | No |
| 66 | No input (transcript/workspace not found) | No |
| 69 | Prerequisite stage missing from manifest | Yes — run the preceding stage first |
| 78 | Config error (`config.json` malformed or invalid) | No |

## Troubleshooting

**"Prerequisite stage missing from manifest"** (exit 69)
- A downstream stage expected an earlier stage to have completed. Check `manifest.jsonl` to see which stages have recorded completion. Run the missing stage first, then retry.

**"Schema validation failed"** (exit 65)
- LLM output did not match the schema. Error message includes field name, expected vs. actual, and a fix hint. Retry the stage or consult the reference file for the stage.

**Rationale quality warnings**
- After stage 1c, you may see warnings about short rationale, fatigue pattern, or dominant-value pattern. These are surfaced in `manifest.jsonl` and displayed to you. Review and approve to continue, or request the labelling stage to be re-run with revised prompts.

**Divergent chunks from self-consistency pass (1b)**
- If `self_consistency_enabled: true` and two passes disagree on a chunk's labels, the divergent chunks are listed in `divergent_chunks.json`. Review and decide whether to accept one pass, merge the labels, or re-run 1b with adjusted prompts.

**Unsourced claims in final output**
- Stage 4v flags any claim in the rewritten output that isn't traceable to a pre-approved extract. Either revise the claim to match an extract, or add the missing extract to the question/answer phase and regenerate the rewrite stage.

## Summary

This skill transforms raw transcripts into faithful, auditable summaries through deterministic scaffolding, parallel LLM agents, explicit human gates, and manifest-based provenance tracking. Every stage is reversible; every interpretation is justified; every claim is sourced.

Use this skill any time you have a transcript (meeting, interview, podcast, legal proceedings, panel discussion, or any other text dialogue or monologue) and you need a structured, faithful summary with full lineage back to source.
