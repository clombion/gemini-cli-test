# Stage 3: Per-Topic QA Extraction

## Overview

Stage 3 transforms pre-generated questions and topic maps into grounded QA extracts by systematically answering each question using only verbatim spans from the source transcript. It consists of two sub-stages:

- **Stage 3 (Per-Topic Extraction)**: Fan-out extraction agents, one per topic, answer pre-generated questions using only source text
- **Stage 3v (Coverage Audit)**: Deterministic + lightweight validation that all chunks and questions are addressed

This stage is LLM-driven (Stage 3) with deterministic and lightweight QA validation (Stage 3v).

---

## Stage 3: Per-Topic QA Extraction Agent

### Purpose

Each topic in the topic map (from Stage 2a) receives its own extraction agent. The agent's task is to answer the set of pre-generated questions (from Stage 2b) using only verbatim or near-verbatim spans extracted from the source chunks assigned to that topic. This ensures tight grounding and eliminates synthesis or hallucination.

### Input

- Topic object from `topic_map.json` (from Stage 2a):
  - `topic_id` (string): Unique identifier
  - `topic_name` (string): Human-readable name
  - `chunk_ids` (array of integers): Chunks belonging to this topic
- Pre-generated questions array from Stage 2b, filtered to those pertaining to this `topic_id`
- Verbatim source text for all chunks in `chunk_ids` (full text, no paraphrasing)
- Chunk metadata including timestamps and speaker information (if available)

### Agent Task

For each question assigned to this topic, the extraction agent must:

1. **Identify source spans**: Locate one or more verbatim text spans in the source chunks that directly answer the question
2. **Extract verbatim**: Copy the span(s) exactly (or with minimal editorial fixes like fixing obvious typos)
3. **Record anchor**: Capture the location: `chunk_index` and `word_range` (start and end word indices within that chunk) so a reader can verify the extract in context
4. **Explain grounding**: Write a rationale (minimum 20 characters) explaining why this span answers the question and how it relates to the question's intent
5. **Mark confidence** (optional): Indicate confidence (0-1) if the answer is partial, ambiguous, or inferred from context rather than directly stated
6. **Handle unanswerable questions**: If the transcript does not contain information needed to answer a question, return `"answer": null` with a rationale explaining why (this is a legitimate finding, not an error)

### Output Schema

Each record conforms to `schemas/stage-3.json`:

```json
{
  "topic_id": "budget_decisions",
  "question": "What is the total budget allocation for Q3 engineering?",
  "answer": "$250,000 for engineering in Q3",
  "answer_anchor": "chunk_index: 5, word_range: [42, 56]",
  "answer_rationale": "Alice states the exact figure in chunk 5. The span directly answers the question of total Q3 engineering budget.",
  "confidence": 0.95
}
```

### Constraints

1. **Verbatim Extraction**: The `answer` must be a direct quote or near-verbatim extract from the source text
   - Acceptable: Exact quote, minor grammar fixes (e.g., "gonna" → "going to"), tense normalization
   - Not acceptable: Paraphrase, synthesis, inference, hallucination
   - If answering requires combining multiple spans, list them separately or quote each in sequence with clear boundaries

2. **Answer Anchor**: Every answer must include a precise location reference
   - Format: `chunk_index: N, word_range: [start, end]`
   - Readers must be able to find the exact span in the source
   - If spanning multiple chunks, list multiple anchors: `chunk_index: 5, word_range: [42, 56]; chunk_index: 6, word_range: [0, 15]`

3. **Answer Rationale**: Minimum 20 characters; explain the connection between the span and the question
   - Example: "Bob explicitly states the timeline in chunk 3. This span directly addresses when the phase is expected to complete."
   - Poor rationale: "Found in source" (not explanatory)
   - Good rationale: "The CFO confirms the budget has been finalized. This span provides the exact amount and eliminates speculation."

4. **Speaker Attribution**: If speaker information is available in the source (e.g., "Alice:", "Bob:"), include it in the answer or rationale
   - Example answer: "Alice: 'We're targeting March 15 for Phase 2'"
   - If multiple speakers contribute to one answer, mention each: "Alice proposes $250K; Bob counters with $200K"

5. **No Paraphrase, No Synthesis**: The span must come directly from the source. Do not:
   - Rephrase to improve clarity
   - Synthesize across chunks unless the source itself makes the connection
   - Fill gaps with assumptions
   - Introduce claims not explicitly stated

6. **Unanswered Questions**: If a question cannot be answered from the transcript:
   - Set `"answer": null`
   - Provide a rationale explaining why (e.g., "The transcript does not contain information about post-launch maintenance plans")
   - Include `"confidence": 0` (or omit confidence)
   - This signals Stage 3v to audit whether the question was poorly formed or the source was incomplete

### Scaffold Structure

Before extraction, generate a scaffold for each topic with the following structure:

```json
{
  "topic_id": "timeline_decisions",
  "topic_name": "Timeline and Milestones",
  "questions": [
    {
      "question_id": "q_1",
      "question": "When are the major phases expected to complete?",
      "answer": null,
      "answer_anchor": null,
      "answer_rationale": null,
      "confidence": null
    },
    {
      "question_id": "q_2",
      "question": "What blockers might delay progress?",
      "answer": null,
      "answer_anchor": null,
      "answer_rationale": null,
      "confidence": null
    }
  ],
  "source_chunks": [
    {
      "chunk_index": 3,
      "timestamp_range": "2:30-4:15",
      "speaker_tags": ["Alice", "Bob"],
      "verbatim_text": "Alice: So we're looking at three phases. Phase 1 in January, Phase 2 should wrap by March 15. Bob: And Phase 3?"
    },
    {
      "chunk_index": 4,
      "timestamp_range": "4:15-6:30",
      "speaker_tags": ["Alice"],
      "verbatim_text": "Alice: Phase 3 is targeted for June, but there are risks. If the DB migration takes longer than expected, we're looking at a two-week slip."
    }
  ]
}
```

**LLM Task**: Fill the `answer`, `answer_anchor`, `answer_rationale`, and optional `confidence` fields for each question based only on the `source_chunks` provided. Do not modify the scaffold structure or add new questions.

### Prompt Structure

```
You are a QA extraction agent for transcript summarization.

Your role: Answer pre-generated questions using only verbatim spans from the source transcript.

Topic: [topic_id, topic_name]

Questions and Scaffold:
[JSON scaffold with null answer fields]

Requirements:
1. For each question, identify a verbatim span from source_chunks that answers it.
2. Record the span exactly (no paraphrase).
3. Document the location: chunk_index and word_range.
4. Provide a rationale (≥20 chars) explaining why this span answers the question.
5. If the transcript does not contain the answer, set answer: null and explain why.
6. All rationales must cite specific source text.
7. Do not synthesize, infer, or hallucinate.
8. Return valid JSON matching schemas/stage-3.json for each extract.

Output: Array of extract records, one per question.
```

### Example Output

```json
[
  {
    "topic_id": "timeline_decisions",
    "question": "When are the major phases expected to complete?",
    "answer": "Phase 1 in January, Phase 2 should wrap by March 15",
    "answer_anchor": "chunk_index: 3, word_range: [25, 45]",
    "answer_rationale": "Alice states the expected completion dates for Phases 1 and 2. This span directly answers the question of major phase timelines.",
    "confidence": 0.98
  },
  {
    "topic_id": "timeline_decisions",
    "question": "What blockers might delay progress?",
    "answer": "If the DB migration takes longer than expected, we're looking at a two-week slip",
    "answer_anchor": "chunk_index: 4, word_range: [18, 37]",
    "answer_rationale": "Alice identifies the primary technical blocker (DB migration duration) and its impact. This span directly addresses the question of potential delays.",
    "confidence": 0.92
  },
  {
    "topic_id": "timeline_decisions",
    "question": "What is the post-launch maintenance plan?",
    "answer": null,
    "answer_anchor": null,
    "answer_rationale": "The transcript does not discuss post-launch maintenance. This gap may indicate an incomplete conversation or a topic not covered in this meeting.",
    "confidence": 0
  }
]
```

---

## Stage 3v: Coverage Audit

### Purpose

After all per-topic extraction agents complete, a coverage audit ensures that:

1. Every chunk from the topic map is referenced in at least one extract (no orphaned chunks)
2. Every question has been answered or explicitly marked as unanswerable
3. No required field in any extract is empty or invalid (deterministic validation)
4. Questions that remain unanswered are flagged for investigation (may indicate retrieval failure, poorly formed questions, or missing content)

### Coverage Checks

#### 1. Chunk Coverage
- **Input**: `topic_map.json` (chunk_ids per topic) and all stage-3 extract records
- **Check**: For each chunk_id in the topic map, verify it appears in at least one `answer_anchor` string in stage-3 extracts
- **Output**: List of orphaned chunks (chunks with no extracts referencing them) or "all chunks covered"
- **Action**: If orphaned chunks exist, flag them for manual review (may indicate the chunk is not relevant to the topic's questions, or the extraction agent missed them)

#### 2. Question Coverage
- **Input**: All pre-generated questions from stage 2b and all stage-3 extract records
- **Check**: For each question, verify that:
  - Either `answer` is not null (answered)
  - Or `answer` is null with a rationale explaining why the transcript lacks this information
- **Output**: List of unanswered questions and their rationales
- **Action**: For each unanswered question, decide:
  - Is this question too vague or out-of-scope? (Potential Stage 2b issue)
  - Did the extraction agent miss relevant source material? (Potential Stage 3 issue; re-run extraction)
  - Is the transcript genuinely missing this information? (Legitimate finding; document in summary)

#### 3. Schema Validation
- **Input**: All stage-3 extract records
- **Checks**:
  - Every required field is present and non-empty (topic_id, question, answer_anchor, answer_rationale)
  - answer_rationale is ≥20 characters
  - answer_anchor is valid (chunk_index is an integer; word_range is [start, end] with start < end)
  - confidence (if present) is a number between 0 and 1
  - No unexpected keys (additionalProperties: false per schema)
- **Output**: List of validation errors (hard failures) or "all records valid"
- **Action**: Hard errors block progression to Stage 4; fix them by re-running Stage 3 with corrected scaffolds or prompt adjustments

#### 4. Quality Warnings
- **Input**: All stage-3 extract records
- **Checks**:
  - `answer` and `answer_rationale` are not suspiciously similar (suggests shallow thinking or copy-paste)
  - `confidence` scores are reasonable (are high-confidence answers well-grounded? Are null answers marked as 0 confidence?)
  - Rationales actually cite specific text from chunks (not vague references)
- **Output**: List of warnings (soft flags; do not block progression)
- **Action**: Review warnings before Stage 4; consider re-running Stage 3 extractions for flagged questions

### Lightweight LLM Check

For each topic, a lightweight agent can audit:
- "Are all pre-generated questions for this topic addressed in the extracts?" (yes/no/partial)
- "Do the extracts logically cover the topic scope?" (yes/no, with brief rationale)
- This is optional and provides high-level confidence before Stage 4

### Audit Output

```json
{
  "coverage_audit": {
    "total_chunks": 15,
    "chunks_referenced": 15,
    "orphaned_chunks": [],
    "total_questions": 8,
    "answered_questions": 6,
    "unanswered_questions": 2,
    "unanswered_details": [
      {
        "question": "What is the post-launch maintenance plan?",
        "topic_id": "timeline_decisions",
        "rationale": "The transcript does not discuss post-launch maintenance."
      },
      {
        "question": "Who is responsible for vendor coordination?",
        "topic_id": "stakeholder_roles",
        "rationale": "Stakeholder roles are mentioned but vendor coordination is not discussed."
      }
    ],
    "schema_errors": [],
    "quality_warnings": [
      {
        "extract_id": "q_3_chunk_7",
        "warning": "answer and answer_rationale are very similar; may indicate shallow extraction"
      }
    ]
  }
}
```

### Manifest Entry

When Stage 3v completes successfully, update the manifest:

```json
{
  "step": "3v",
  "status": "completed",
  "timestamp": "2025-03-11T14:22:00Z",
  "coverage_audit": {
    "chunks_referenced": 15,
    "orphaned_chunks": 0,
    "answered_questions": 6,
    "unanswered_questions": 2,
    "schema_errors": 0,
    "quality_warnings": 1
  },
  "notes": "2 questions marked unanswerable (not in source). All chunks referenced. 1 quality warning for review."
}
```

---

## Relationship to Other Stages

- **Input from Stage 2**: Topic map (2a) and pre-generated questions (2b, approved by 2c)
- **Output to Stage 4**: Grounded QA extracts become source material for rewriting
- **Audit trail**: Every claim in the final summary can be traced back through these extracts to a specific chunk and word range in the source

This tight grounding eliminates hallucination risk and makes the audit trail transparent to human reviewers in Stage 4g (human gate).
