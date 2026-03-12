# Stage 2: Topic Merging and Question Generation

## Overview

Stage 2 transforms chunk-level labels into document-level topic structure and generates guiding questions. It consists of three sub-stages:

- **Stage 2a (Topic Merging)**: Cluster micro-topics (chunk-level tags) into macro-topics with descriptions
- **Stage 2b (Question Generation)**: Generate specific, grounded questions for each topic
- **Stage 2c (Human Gate)**: Human review and approval of topic map and questions

This stage is LLM-driven (2a and 2b) with mandatory human validation (2c).

---

## Stage 2a: Topic Merging Agent

### Purpose

Chunk labels from Stage 1 contain fine-grained topic_tags (e.g., "budget_discussion", "timeline_negotiation"). Topic merging clusters these into broader, document-level topics that group related chunks.

### Input

- `labels_pass1.jsonl` (all chunk label records from Stage 1a)
- Topic vocabulary from all `topic_tags` fields
- If self-consistency enabled: divergent chunk list from Stage 1b

### Agent Task

Read all chunk labels and produce a topic map: an array of topic objects, each grouping 2-10 related chunks.

#### Topic Map Schema

Each topic object conforms to `schemas/stage-2a.json`:

```json
{
  "topic_id": "budget_decisions",
  "topic_name": "Budget Allocation Decisions",
  "description": "Discussions where Alice and Bob propose, negotiate, and finalize specific budget amounts for Q3, including engineering and marketing allocations.",
  "chunk_ids": [3, 5, 7, 12],
  "is_divergent_merge": false
}
```

### Merging Rules

1. **Cluster Size**: Each topic should contain 2-10 chunks
   - Fewer than 2: too fragmented; merge with nearest topic
   - More than 10: consider splitting or relabeling as overgeneralized
   - Sweet spot: 3-7 chunks per topic

2. **Topic Names**: Concise, human-readable (3-5 words)
   - Not: "Discussions about budget and timelines and resources"
   - Yes: "Budget Allocation Decisions"

3. **Descriptions**: Summarize what the topic contains and why chunks belong together
   - Example: "Discussions where participants propose and negotiate quarterly budget numbers for engineering and marketing teams."

4. **Divergent Chunks** (if self-consistency enabled):
   - If a chunk is flagged as divergent (different topic_tags in pass 1 vs. pass 2), place it in a topic and mark `is_divergent_merge = true`
   - This signals Stage 2c to pay special attention during review

### Bottom-Up Discovery Approach

**Do not start with top-down framing** (e.g., "I expect this transcript to discuss X, Y, Z"). Instead:

1. **Extract**: Collect all unique `topic_tags` from chunks
2. **Group**: Cluster similar tags (e.g., "budget_discussion", "budget_finalization" → same macro-topic)
3. **Assign**: Group chunks by their tag memberships
4. **Name**: Derive topic names from the clustered vocabulary
5. **Cross-cutting threads**: Scan `key_entities`, `speaker_intent`, and `raw_source_text` for recurring rhetorical devices that span multiple thematic topics — metaphors, analogies, examples, or narrative arcs that reappear across chunks with different primary tags. Each such thread becomes its own topic (a chunk may appear in both a thematic topic and a cross-cutting topic).
6. **Validate**: Ensure no orphaned chunks, cluster size guidelines respected

### Prompt Structure

```
You are a topic merging agent for transcript summarization.

Your role: Read chunk labels and cluster micro-topics into macro-topics.

Input: Array of chunk label records from Stage 1a
[all records from labels_pass1.jsonl]

Task:
1. Extract all unique topic_tags across all chunks.
2. Group similar tags together (e.g., "budget_planning" + "resource_allocation" might share a macro-topic).
3. Create a topic map where each topic groups 2-10 related chunks.
4. For each topic:
   - Assign a unique topic_id (alphanumeric + underscore)
   - Name it concisely (3-5 words)
   - Describe what chunks it contains and why they belong together
   - List the chunk_ids
5. Scan for cross-cutting rhetorical threads. These are recurring metaphors, analogies, examples, or narrative arcs that appear across chunks in different thematic topics. Detection heuristic: look for the same imagery, analogy, or named example appearing in 3+ chunks that belong to different tag clusters. Check key_entities for repeated metaphor names, speaker_intent for repeated explanatory patterns, and raw_source_text for repeated phrases or imagery. Each such thread becomes its own topic (chunks may appear in both a thematic topic and a cross-cutting topic).

6. Do not use top-down framing. Start with the chunk-level vocabulary; let topics emerge from the data.

Requirements:
- Output array of topic objects matching schemas/stage-2a.json
- Each topic_id must be unique and alphanumeric + underscore
- Cluster sizes: 2-10 chunks per topic (flag if outside this range)
- Descriptions must cite specific topic_tags or conversational functions from chunks
- All chunk_ids from Stage 1 must appear in at least one topic (coverage gate)
- If divergent chunks provided: mark is_divergent_merge = true if topic includes any
```

### Example: Topic Merging

#### Input Chunks (Simplified)

```json
[
  {
    "chunk_index": 3,
    "topic_tags": ["budget_discussion", "resource_allocation"],
    "conversational_function": "question"
  },
  {
    "chunk_index": 5,
    "topic_tags": ["budget_finalization", "q3_planning", "resource_allocation"],
    "conversational_function": "decision"
  },
  {
    "chunk_index": 7,
    "topic_tags": ["timeline_planning", "milestone_setting"],
    "conversational_function": "decision"
  },
  {
    "chunk_index": 8,
    "topic_tags": ["timeline_planning", "dependency_analysis"],
    "conversational_function": "elaboration"
  }
]
```

#### Output Topic Map

```json
[
  {
    "topic_id": "budget_decisions",
    "topic_name": "Budget Allocation Decisions",
    "description": "Chunks where participants discuss, propose, and finalize Q3 budget amounts for different departments. Includes resource allocation discussions and final commitment to budget figures.",
    "chunk_ids": [3, 5],
    "is_divergent_merge": false
  },
  {
    "topic_id": "timeline_milestones",
    "topic_name": "Timeline and Milestone Planning",
    "description": "Chunks where participants set milestones and analyze dependencies for project execution. Includes specific timeline commitments and technical dependency discussions.",
    "chunk_ids": [7, 8],
    "is_divergent_merge": false
  }
]
```

### Example: Cross-Cutting Rhetorical Thread

A lecture transcript has 6 thematic topics. The instructor uses a "building a house" metaphor that recurs across 4 of them:

- Chunk 4 (topic: `project_planning`) — introduces the metaphor: "you need blueprints before you start pouring concrete"
- Chunk 12 (topic: `team_roles`) — extends it: "the architect doesn't lay bricks, but without the blueprint the bricklayer builds a wall in the wrong place"
- Chunk 19 (topic: `quality_assurance`) — applies it to testing: "you wouldn't move into a house without checking the plumbing"
- Chunk 25 (topic: `stakeholder_management`) — closes the arc: "the client who changes the blueprint after the roof is on pays for two houses"

**Detection**: `key_entities` in chunks 4, 12, 19, 25 all contain "house_metaphor" or "blueprint", but these chunks belong to 4 different tag clusters. The same imagery appears in 4+ chunks across different thematic topics → cross-cutting thread.

**Output**: Add a 7th topic with dual membership:

```json
{
  "topic_id": "house_building_metaphor",
  "topic_name": "The House-Building Metaphor",
  "description": "Cross-cutting rhetorical device threading through the lecture. Introduced to explain planning necessity (blueprints before concrete), extended to role clarity (architect vs bricklayer), applied to quality (checking plumbing before moving in), and closed with scope creep consequences (changing blueprints after the roof). Chunks also belong to their primary thematic topics.",
  "chunk_ids": [4, 12, 19, 25],
  "is_divergent_merge": false
}
```

The chunks remain in their thematic topics (`project_planning`, `team_roles`, etc.) and additionally appear in the cross-cutting topic. Questions for this topic should track how the metaphor evolves and what each extension explains.

---

## Stage 2b: Question Generation Agent

### Purpose

For each topic in the map, generate 3-6 specific questions that:
- Ground extraction criteria for downstream stages
- Define faithfulness evaluation metrics
- Clarify what stakeholders care about in that topic

### Input

- Topic map from Stage 2a (including topic_id, description, chunk_ids)
- Original chunks and their labels (for grounding)

### Agent Task

For each topic, generate questions that are:
1. **Grounded**: Rooted in what the chunk content actually discusses
2. **Specific**: Not generic (e.g., not "What happened?" but "What was agreed?")
3. **Actionable**: Guide extraction and verification in downstream stages

### Output Schema

Each question set conforms to `schemas/stage-2b.json`:

```json
{
  "topic_id": "budget_decisions",
  "questions": [
    "What specific budget amounts were proposed for each department?",
    "Were any proposed amounts contested or negotiated?",
    "What was the final approved budget allocation, if any?",
    "Were there any constraints or conditions placed on the budget?",
    "Who had decision-making authority over the final allocation?"
  ]
}
```

#### Schema Constraints

- `questions` is an array of 3-6 strings
- Each question must end with `?` (regex: `\?$`)
- Each question must be non-empty (minLength: 1)

### Question Generation Guidelines

#### Example 1: Budget Topic

**Topic Description**: "Budget allocation decisions for Q3 including proposed figures, negotiation, and final approval."

**Grounded Questions**:
- "What specific amounts were proposed for each department?" (addresses "proposed figures")
- "Were any figures contested or changed during negotiation?" (addresses "negotiation")
- "What was agreed as the final Q3 budget?" (addresses "final approval")
- "What constraints or conditions apply to the approved amounts?" (addresses potential hidden complexity)
- "Which person(s) or role(s) had final decision authority?" (addresses governance)

#### Example 2: Timeline Topic

**Topic Description**: "Participants set project milestones and identify technical dependencies affecting execution schedule."

**Grounded Questions**:
- "What milestones were explicitly set?" (addresses "set project milestones")
- "What are the identified dependencies and their impact on timing?" (addresses "technical dependencies")
- "Were any dependencies flagged as risks or constraints?" (addresses potential delays)
- "Who is responsible for each milestone?" (addresses accountability)
- "Are there any conditional milestones (e.g., dependent on prior approvals)?" (addresses complexity)

#### Example 3: Decision Topic

**Topic Description**: "Participants reach consensus on a strategic direction after evaluating options."

**Grounded Questions**:
- "What options were explicitly considered?" (addresses "evaluating options")
- "What were the key trade-offs discussed?" (addresses decision reasoning)
- "What consensus was reached, and by whom?" (addresses outcome)
- "Were any options explicitly rejected? Why?" (addresses alternatives)
- "What next steps or action items follow from this decision?" (addresses follow-through)

### Prompt Structure

```
You are a question generation agent for transcript summarization.

Your role: Generate 3-6 grounded questions for each topic to guide extraction and evaluation.

Input:
- Topic map entry (topic_id, topic_name, description, chunk_ids)
- Chunk labels and raw text from those chunks

Task:
For this topic, generate 3-6 specific questions that:
1. Are grounded in the chunk content (cite specific conversational functions or entities)
2. Define what stakeholders care about in this topic
3. Guide extraction (what facts or quotes should be extracted?)
4. Guide evaluation (how will we know if a summary is faithful?)

Requirements:
- Each question must end with ? (regex: \?$)
- 3-6 questions per topic (no more, no fewer)
- Questions must be grounded in the description and chunks, not generic
- No yes/no questions (ask "What was agreed?" not "Was it agreed?")
- Output JSON matching schemas/stage-2b.json

Topic:
{topic_id, topic_name, description, chunk_ids}

Relevant chunks:
[raw_source_text and topic_tags from those chunks]

Generate the questions:
```

---

## Stage 2c: Human Gate

### Purpose

Human operator reviews and approves the topic map and question set before proceeding to extraction (Stage 3).

### Review Checklist

1. **Topic Coverage**: Do the topics capture all significant content from the transcript?
   - Any major themes missing?
   - Any off-topic or minor topics that shouldn't be included?

2. **Topic Granularity**: Is clustering appropriate?
   - Are any topics too broad (overgeneralized)?
   - Are any topics too narrow (fragmented)?
   - Consider: 2-10 chunks per topic; 3-5 word names

3. **Cross-Cutting Threads**: Are there recurring metaphors, analogies, examples, or narrative arcs that thread across multiple thematic topics?
   - Scan for repeated imagery or phrases that appear in chunks with different primary tags
   - If found, these should have their own topic tracking the device's evolution across the transcript
   - A chunk can belong to both a thematic topic and a cross-cutting topic

4. **Divergent Chunks**: If self-consistency enabled, review any topics marked `is_divergent_merge = true`
   - Why was this chunk divergent?
   - Is it correctly placed in this topic?
   - Should it be split off or merged differently?

5. **Question Quality**: Are questions well-grounded and actionable?
   - Do questions reflect what's at stake in the topic?
   - Are they specific enough to guide extraction?
   - Are any questions vague or generic?

### Approval Actions

#### Option 1: Approve As-Is
Mark manifest: `{"stage": "2c", "approval": true, "comment": "OK"}`
Proceed to Stage 3.

#### Option 2: Suggest Merges
```json
{
  "stage": "2c",
  "approval": false,
  "suggested_changes": [
    {
      "action": "merge",
      "topic_ids": ["timeline_planning", "dependency_analysis"],
      "reason": "These are closely related; merge into single 'Timeline & Dependencies' topic"
    }
  ],
  "comment": "After merging topics as above, should be good to proceed"
}
```

Agent re-runs Stage 2a with feedback, then re-submits.

#### Option 3: Revise Questions
```json
{
  "stage": "2c",
  "approval": false,
  "suggested_changes": [
    {
      "topic_id": "budget_decisions",
      "revised_questions": [
        "What specific amounts were proposed for engineering and marketing?",
        "Were any proposed amounts contested?",
        "What was the final approved allocation?",
        "Were there explicit constraints on how the budget could be spent?"
      ]
    }
  ],
  "comment": "Questions were too vague; revised to be more specific to this transcript"
}
```

Agent revises Stage 2b questions and re-submits.

#### Option 4: Conditional Approval
```json
{
  "stage": "2c",
  "approval": true,
  "conditions": [
    "Divergent chunk 12 should be spot-checked in Stage 3 extraction",
    "Question about 'constraints' in budget_decisions topic may be overspecified for this transcript"
  ],
  "comment": "Approved; note conditions for downstream review"
}
```

Proceed to Stage 3 with flags recorded.

### Coverage Gate (Deterministic)

Before human approval is requested, verify coverage:

```bash
scripts/validate_coverage.py --stage 2c --topic_map topic_map.json --manifest manifest.json
```

Output:
```
Total chunks from Stage 1: 42
Chunks in topic map: 42
Coverage: 100%

Result: PASS
Proceed to human gate.
```

If coverage < 100%:
```
ERROR: Uncovered chunks
  Total chunks: 42
  In topic map: 38
  Missing: [15, 23, 29, 31]

Action: Re-run topic merging agent; ensure all chunks are assigned.
```

### Manifest Update

When human approves, record:

```json
{
  "stage": "2c",
  "timestamp": "2026-03-11T14:32:00Z",
  "approval": true,
  "approved_by": "human",
  "topic_count": 8,
  "total_chunks": 42,
  "notes": "Topics well-clustered; questions grounded. No divergent chunks. Ready for extraction."
}
```

---

## Quality Checklist for Stage 2

- [ ] Topic map clusters 2-10 chunks per topic (validate for outliers)
- [ ] All chunk IDs from Stage 1 appear in at least one topic (coverage gate passes)
- [ ] Topic names are concise (3-5 words) and human-readable
- [ ] Topic descriptions summarize content and rationale for grouping
- [ ] Question sets have 3-6 questions per topic
- [ ] All questions end with `?` and are grounded in chunk content
- [ ] Questions are specific (not generic) and actionable
- [ ] Cross-cutting rhetorical threads (metaphors, analogies, narrative arcs) identified as separate topics
- [ ] If self-consistency enabled: divergent chunks reviewed and marked
- [ ] Human gate completed: topic map and questions approved
- [ ] Manifest updated with human approval
- [ ] Proceed to Stage 3

---

## Example: Complete Stage 2 Workflow

### Stage 2a Output: Topic Map

```json
[
  {
    "topic_id": "q3_budget_allocation",
    "topic_name": "Q3 Budget Allocation Decisions",
    "description": "Alice and Bob propose, negotiate, and finalize specific budget amounts for engineering and marketing departments. Includes discussion of specific figures ($500K engineering, $200K marketing) and confirmation of approval.",
    "chunk_ids": [3, 5, 9],
    "is_divergent_merge": false
  },
  {
    "topic_id": "project_timeline",
    "topic_name": "Project Timeline and Milestones",
    "description": "Participants set execution milestones for Q3 and identify technical dependencies. Includes discussion of Go-Live date, testing phases, and integration dependencies.",
    "chunk_ids": [12, 14, 16, 18],
    "is_divergent_merge": false
  },
  {
    "topic_id": "resource_constraints",
    "topic_name": "Resource Constraints and Risks",
    "description": "Discussion of staffing constraints, third-party dependencies, and identified risks to project success.",
    "chunk_ids": [21, 24],
    "is_divergent_merge": true
  }
]
```

### Stage 2b Output: Questions

```json
[
  {
    "topic_id": "q3_budget_allocation",
    "questions": [
      "What specific budget amounts were proposed for engineering and marketing?",
      "Were the proposed amounts contested or modified during discussion?",
      "What was the final approved Q3 budget allocation?",
      "Were any constraints or conditions placed on budget spending?"
    ]
  },
  {
    "topic_id": "project_timeline",
    "questions": [
      "What are the key milestones and their target dates?",
      "What technical dependencies were identified as critical?",
      "Which dependencies are on the critical path and why?",
      "Who is responsible for managing each milestone?",
      "Are there any conditional milestones or contingency timelines?"
    ]
  },
  {
    "topic_id": "resource_constraints",
    "questions": [
      "What staffing constraints were identified?",
      "What external or third-party dependencies exist?",
      "What risks were explicitly flagged, and what is their impact?",
      "What mitigation strategies were discussed?"
    ]
  }
]
```

### Stage 2c: Human Approval

```json
{
  "stage": "2c",
  "timestamp": "2026-03-11T15:45:00Z",
  "approval": true,
  "approved_by": "john.doe",
  "topic_count": 3,
  "total_chunks": 42,
  "notes": "Topics are well-structured and grounded in chunk-level labels. Note: Topic 'resource_constraints' (chunk IDs 21, 24) is marked divergent; confirmed the divergence reflects genuine ambiguity about risk categorization. Questions are specific and actionable. Ready to proceed to Stage 3 extraction."
}
```

---

## References

- `schemas/stage-2a.json` — Topic map entry schema
- `schemas/stage-2b.json` — Question set per topic schema
- `labels_pass1.jsonl` — Chunk label records from Stage 1a (input to 2a)
- Stage 1b self-consistency report (if enabled) — lists divergent chunks
