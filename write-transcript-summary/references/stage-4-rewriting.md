# Stage 4: Rewriting and QA Grounding

## Overview

Stage 4 transforms grounded QA extracts from Stage 3 into a readable summary by rewriting extracted answers as flowing prose. It consists of two sub-stages:

- **Stage 4 (Rewriting)**: Fan-out rewriting agents, one per topic, compose prose sections from QA extracts with strict faithfulness constraints
- **Stage 4v (QA Grounding Loop)**: Separate validation pass verifies that all draft claims are traceable to pre-approved questions and source extracts
- **Stage 4g (Human Gate)**: Human review and approval of the final summary against the transcript

This stage is LLM-driven (Stage 4) with deterministic validation (Stage 4v) and mandatory human review (Stage 4g).

---

## Stage 4: Rewriting Agent

### Purpose

The rewriting agent transforms structured QA extracts (from Stage 3) into flowing prose sections organized by topic. Unlike earlier stages, the rewriter composes **readable text**, but strictly from information contained in the provided extracts. No synthesis, inference, or new claims are permitted.

### Input

For each topic, the rewriting agent receives a **rewriting scaffold** containing:

```json
{
  "topic_id": "timeline_decisions",
  "topic_name": "Timeline and Milestones",
  "questions": [
    "When are the major phases expected to complete?",
    "What blockers might delay progress?"
  ],
  "source_segments": [
    {
      "chunk_index": 3,
      "timestamp_range": "2:30-4:15",
      "speaker": "Alice",
      "verbatim_extract": "Phase 1 in January, Phase 2 should wrap by March 15",
      "question_answered": 0,
      "answer_anchor": "word_range: [25, 45]"
    },
    {
      "chunk_index": 3,
      "timestamp_range": "2:30-4:15",
      "speaker": "Alice",
      "verbatim_extract": "If the DB migration takes longer than expected, we're looking at a two-week slip",
      "question_answered": 1,
      "answer_anchor": "word_range: [18, 37]"
    }
  ]
}
```

Where:
- `topic_id` and `topic_name` are deterministically set (from Stage 2a topic map)
- `questions` is the filtered list of questions for this topic (from Stage 2b)
- `source_segments` is the array of QA extracts (from Stage 3) with anchors preserved for later verification
- `question_answered` index maps each segment to the question it answers (for traceability)

### Agent Task

Compose a prose section that incorporates all `source_segments` into a coherent narrative. The agent must:

1. **Use only provided extracts**: Write prose based exclusively on the `verbatim_extract` values in `source_segments`
2. **Preserve all information**: Ensure every extract is reflected or cited in the prose
3. **Avoid synthesis**: Do not combine segments to draw new conclusions; report what each segment says
4. **Maintain order** (optional): Segments are ordered by chunk index; preserve this order if it aids coherence
5. **Include speaker attribution** (if available): If a single speaker dominates a segment, attribution can be implicit; if multiple speakers appear, explicitly distinguish
6. **Write naturally**: Prose should flow and be readable, not a list of disconnected quotes
7. **Handle ambiguity**: If source is ambiguous or incomplete, say so explicitly rather than inferring

### Output Schema

Each rewritten section conforms to `schemas/stage-4.json`:

```json
{
  "topic_id": "timeline_decisions",
  "topic_name": "Timeline and Milestones",
  "section_text": "The project is structured in three phases. Phase 1 is scheduled for January, with Phase 2 expected to wrap by March 15. The main risk to the timeline is the database migration. If this takes longer than expected, the entire schedule could slip by approximately two weeks.",
  "source_segment_count": 2,
  "has_unsupported_claims": false
}
```

### Constraints

1. **Faithfulness**: Every statement in the prose must be directly traceable to a `source_segment` in the scaffold
   - Acceptable: Synthesizing multiple extracts into a unified narrative ("Phase 1 is in January; Phase 2 by March 15" from two extracts about each phase)
   - Not acceptable: Adding interpretation ("Phase 1 is early, Phase 2 is on track" - the "on track" claim is not in source)
   - Test: For every sentence, ask "Can I point to which extract(s) support this?"

2. **Completeness**: All provided `source_segments` must appear in the prose
   - Either directly quoted or paraphrased
   - If a segment is tangential, note it but do not omit it
   - Empty sections indicate a problem (either scaffolding included irrelevant extracts, or the agent failed)

3. **No New Claims**: Introduce no information not present in `source_segments`
   - Not allowed: Adding context from earlier topics, general knowledge, or assumptions
   - Not allowed: Inferring causation where extracts only describe sequence
   - Allowed: Connecting two extracts with transitional prose ("Given this timeline, the migration risk becomes critical")

4. **Speaker Attribution**
   - If a `source_segment` has a single speaker, you can use implicit attribution: "The timeline is three phases: Phase 1 in January, Phase 2 by March 15" (from Alice)
   - If multiple speakers appear in different segments, distinguish them: "Alice proposes Phase 2 by March 15, while Bob suggests end of month as a safer target"
   - If attribution is unclear or mixed, err on the side of explicit: "According to the participants, Phase 2 is expected by March 15"

5. **Prose Style**
   - Aim for 1-2 sentence per QA pair (or per cluster of related extracts)
   - Avoid bullet points unless necessary
   - Avoid quotation marks unless quoting directly; paraphrase when possible
   - Use natural transitions: "In addition", "However", "As a result"

6. **Ambiguity and Incompleteness**
   - If source extracts are ambiguous, preserve the ambiguity: "The timeline is either March 15 or end of month, depending on engineering capacity"
   - If source is sparse, say so: "While Phase 1 is confirmed for January, the details of Phases 2 and 3 remain unclear"
   - Do not fill gaps with assumptions

### Prompt Structure

```
You are a rewriting agent for transcript summarization.

Your role: Compose readable prose from QA extracts while adhering strictly to provided information.

Topic: [topic_name]
Pre-Approved Questions: [list of questions]

Scaffold with Source Segments:
[JSON scaffold with verbatim_extract and question_answered index]

Task:
1. Compose a prose section that incorporates all source_segments.
2. Use only information in source_segments; do not infer, synthesize, or add context.
3. Preserve all provided information; ensure every segment is reflected in the prose.
4. Use natural transitions and paragraph structure; aim for readability.
5. Include speaker attribution if available and helpful.
6. If source is ambiguous or incomplete, say so explicitly.
7. Test each sentence: "Can I point to which extract(s) support this?"

Requirements:
- Do not introduce claims not present in source_segments.
- Do not synthesize across segments unless the source itself makes the connection.
- Return valid JSON matching schemas/stage-4.json.
- section_text should be plain prose (no markdown headers, no bullet points unless source contains them).
- source_segment_count should equal the number of extracts incorporated.

Output: Single record with topic_id, topic_name, section_text, source_segment_count, has_unsupported_claims.
```

### Example Output

**Input Scaffold** (Stage 4 input):
```json
{
  "topic_id": "timeline_decisions",
  "topic_name": "Timeline and Milestones",
  "questions": [
    "When are the major phases expected to complete?",
    "What blockers might delay progress?"
  ],
  "source_segments": [
    {
      "chunk_index": 3,
      "timestamp_range": "2:30-4:15",
      "speaker": "Alice",
      "verbatim_extract": "Phase 1 in January, Phase 2 should wrap by March 15",
      "question_answered": 0
    },
    {
      "chunk_index": 4,
      "timestamp_range": "4:15-6:30",
      "speaker": "Alice",
      "verbatim_extract": "Phase 3 is targeted for June, but there are risks. If the DB migration takes longer than expected, we're looking at a two-week slip.",
      "question_answered": 0
    },
    {
      "chunk_index": 4,
      "timestamp_range": "4:15-6:30",
      "speaker": "Alice",
      "verbatim_extract": "If the DB migration takes longer than expected, we're looking at a two-week slip",
      "question_answered": 1
    }
  ]
}
```

**Output** (Stage 4 output):
```json
{
  "topic_id": "timeline_decisions",
  "topic_name": "Timeline and Milestones",
  "section_text": "The project follows a three-phase schedule. Phase 1 is targeted for January, with Phase 2 wrapping by March 15. Phase 3 is scheduled for June. The primary risk to this timeline is the database migration. If the migration takes longer than expected, the team anticipates a two-week delay to the overall schedule.",
  "source_segment_count": 3,
  "has_unsupported_claims": false
}
```

---

## Stage 4v: QA Grounding Loop

### Purpose

After rewriting agents complete, a **separate validation pass** verifies that every claim in the draft prose is grounded in the pre-approved question set and source extracts. This prevents drift where a rewriter introduces new claims not supported by the QA pairs.

### Validation Process

The grounding loop is a **deterministic + lightweight LLM check**:

1. **Extract Claims**: For each rewritten section, identify all factual claims (e.g., "Phase 1 is January", "DB migration is a risk", "two-week delay possible")
2. **Check Against Questions**: For each claim, ask: "Is this claim answering one of the pre-approved questions for this topic?"
3. **Check Against Extracts**: For each claim, ask: "Can I point to a `source_segment` (from Stage 3) that supports this claim?"
4. **Identify Gaps**: Flag any claim that cannot be traced to either:
   - A pre-approved question (suggests off-topic prose)
   - A source extract with an anchor (suggests unsupported inference)

### Grounding Loop Input

```json
{
  "topic_id": "timeline_decisions",
  "topic_name": "Timeline and Milestones",
  "pre_approved_questions": [
    "When are the major phases expected to complete?",
    "What blockers might delay progress?"
  ],
  "draft_section_text": "The project follows a three-phase schedule. Phase 1 is targeted for January, with Phase 2 wrapping by March 15. Phase 3 is scheduled for June. The primary risk to this timeline is the database migration. If the migration takes longer than expected, the team anticipates a two-week delay to the overall schedule.",
  "source_extracts": [
    {
      "extract_id": "q_0_chunk_3",
      "question": "When are the major phases expected to complete?",
      "answer": "Phase 1 in January, Phase 2 should wrap by March 15",
      "answer_anchor": "chunk_index: 3, word_range: [25, 45]"
    },
    {
      "extract_id": "q_0_chunk_4",
      "question": "When are the major phases expected to complete?",
      "answer": "Phase 3 is targeted for June, but there are risks.",
      "answer_anchor": "chunk_index: 4, word_range: [52, 70]"
    },
    {
      "extract_id": "q_1_chunk_4",
      "question": "What blockers might delay progress?",
      "answer": "If the DB migration takes longer than expected, we're looking at a two-week slip",
      "answer_anchor": "chunk_index: 4, word_range: [18, 37]"
    }
  ]
}
```

### Grounding Loop Output

```json
{
  "topic_id": "timeline_decisions",
  "grounding_check": {
    "total_claims": 5,
    "grounded_claims": 5,
    "ungrounded_claims": [],
    "off_topic_claims": [],
    "status": "approved"
  }
}
```

Or, if issues are found:

```json
{
  "topic_id": "timeline_decisions",
  "grounding_check": {
    "total_claims": 6,
    "grounded_claims": 5,
    "ungrounded_claims": [
      {
        "claim": "Phase 3 is the most complex phase",
        "reason": "No source extract supports this claim. It is not answering a pre-approved question."
      }
    ],
    "off_topic_claims": [],
    "status": "issues_found"
  }
}
```

### Remediation

If the grounding loop finds issues:
1. **Ungrounded claims**: Remove or revise to cite a source extract
2. **Off-topic prose**: Either align with a pre-approved question or move to a different topic
3. **Missing questions**: If a claim is valid but no question exists to support it, this signals a Stage 2b gap (question generation was incomplete)

Option: Re-run the rewriting agent with the grounding issues highlighted, asking it to revise.

### Grounding Loop Prompt

```
You are a QA grounding validator for transcript summarization.

Your role: Verify that every claim in the rewritten prose is traceable to pre-approved questions and source extracts.

Topic: [topic_name]
Pre-Approved Questions: [list of questions]
Draft Prose: [section_text]
Source Extracts: [array of QA extracts with anchors]

Task:
1. Identify all factual claims in the draft prose.
2. For each claim, determine:
   a. Which pre-approved question(s) does this claim answer?
   b. Which source extract(s) support this claim?
3. Flag claims that:
   - Cannot be mapped to any pre-approved question
   - Cannot be mapped to any source extract
   - Represent inference or synthesis not supported by source

Output: Grounding check record with total_claims, grounded_claims, ungrounded_claims, status.
```

---

## Stage 4g: Human Gate

### Purpose

Before finalizing the summary, a human reviewer verifies that:

1. The rewritten prose faithfully represents the transcript
2. No claims have been injected that are not grounded in source
3. The manifest audit trail is complete (every chunk, every question, every extract, every claim is traceable)

### Human Review Checklist

- [ ] **Faithfulness**: Does the summary accurately represent what was said in the transcript?
- [ ] **Completeness**: Are all major topics from the topic map covered?
- [ ] **Ambiguity**: Are there any claims that seem to go beyond what the transcript stated?
- [ ] **Attribution**: Are speakers clearly identified where relevant?
- [ ] **Manifest Audit Trail**: Can I trace every claim back to a source chunk?
  - Click on any claim → find the QA extract that supports it
  - Click on the extract → find the source chunk in the transcript
  - Verify the verbatim span is in the chunk

### Manifest Audit Trail

The manifest records every step of the process:

```json
{
  "summary_id": "meeting_2025-03-11",
  "transcript_file": "/path/to/transcript.txt",
  "manifest": [
    {
      "step": "0c",
      "status": "completed",
      "output_file": "chunk_config.json"
    },
    {
      "step": "1a",
      "status": "completed",
      "output_file": "labels_pass1.jsonl",
      "chunk_count": 15
    },
    {
      "step": "2a",
      "status": "completed",
      "output_file": "topic_map.json",
      "topic_count": 8
    },
    {
      "step": "2b",
      "status": "completed",
      "output_file": "questions.jsonl",
      "question_count": 20
    },
    {
      "step": "3",
      "status": "completed",
      "output_file": "extracts.jsonl",
      "extract_count": 20,
      "coverage": {
        "chunks_referenced": 15,
        "questions_answered": 18,
        "questions_unanswerable": 2
      }
    },
    {
      "step": "3v",
      "status": "completed",
      "output_file": "coverage_audit.json"
    },
    {
      "step": "4",
      "status": "completed",
      "output_file": "sections.jsonl",
      "section_count": 8
    },
    {
      "step": "4v",
      "status": "completed",
      "output_file": "grounding_checks.jsonl"
    },
    {
      "step": "4g",
      "status": "pending_human_review",
      "output_file": "summary.md"
    }
  ]
}
```

When the human approves the summary:

```json
{
  "step": "4g",
  "status": "completed",
  "timestamp": "2025-03-11T15:45:00Z",
  "reviewer": "alice@example.com",
  "approved": true,
  "notes": "Summary faithfully represents the meeting. All major topics covered. Manifest audit trail verified.",
  "output_file": "summary.md"
}
```

### Final Summary Output

The final summary (`output/summary.md`) is a human-readable markdown document organized by topic:

```markdown
# Meeting Summary: Project Planning (2025-03-11)

## Timeline and Milestones

The project follows a three-phase schedule. Phase 1 is targeted for January, with Phase 2 wrapping by March 15. Phase 3 is scheduled for June. The primary risk to this timeline is the database migration. If the migration takes longer than expected, the team anticipates a two-week delay to the overall schedule.

## Budget Allocation

...

## Stakeholder Roles

...
```

---

## Relationship to Other Stages

- **Input from Stage 3**: Grounded QA extracts with anchors (word ranges)
- **Scaffolding from Stage 2**: Topic map and pre-approved questions provide the structure and approval gates
- **Output to Stage 4g**: Final readable summary (`output/summary.md`)
- **Audit trail**: Every section can be traced back through extracts to source chunks and questions

The rewriting stage deliberately decouples **composition** (how to write readable prose) from **grounding** (proving every claim is sourced). This separation allows:
- Rewriters to focus on readability without worrying about verification
- Validators to focus on faithfulness without worrying about prose quality
- Humans to review the finished product with confidence that all claims are auditable

---

## Quality Requirements

- Prose is readable and flows naturally, not stilted or list-like
- Every claim is directly traceable to a QA extract and source chunk
- No synthesis or inference beyond what the source extracts contain
- Speaker attribution is clear and accurate
- The final summary is faithful to the transcript while more concise and organized
