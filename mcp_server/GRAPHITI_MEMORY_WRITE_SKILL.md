# Memory Write Skill

This skill is invoked at the end of a conversation to produce a single high-quality `add_memory` entry.

**Hard rule: you MUST output a `memory-draft` block in the chat before calling `add_memory`. No visible draft → no tool call.**

---

## Why this matters — how the backend processes your input

When you call `add_memory`, the `episode_body` text is sent to a backend LLM that builds graph structure from it. Understanding this pipeline lets you write text that produces useful graphs instead of noise.

The pipeline has four stages:

1. **Extract nodes** — The backend scans your text and turns every noun phrase it considers a "significant entity" into a graph node. The node's `name` is the exact text the backend picked out.
2. **Classify nodes** — Each node is matched against configured entity types: Anchor, WorkItem, Issue, Decision, Verification. If none fit, the node becomes a bare `Entity` with no subtype — almost always useless noise.
3. **Extract edges** — For every pair of nodes that have a stated relationship in your text, the backend creates a directed edge with a `relation_type` (SCREAMING_SNAKE_CASE verb) and a `fact` (natural-language paraphrase).
4. **Deduplicate** — New nodes are compared against existing graph nodes. If the backend judges two nodes refer to the same thing, they merge. Vague names like "Memory persistence" rarely match anything and accumulate as orphans.

**Consequence: your `episode_body` is a program that creates graph structure.** Every noun you write may become a node. Every verb connecting two nouns may become an edge. Write with that awareness.

### The three fields and where they go

| Field | Fed to extraction? | Indexed for search? | Use it for |
|---|---|---|---|
| `episode_body` | **Yes** — backend LLM extracts nodes and edges from this | Embedding + BM25 | Fact triples only. Every word here shapes the graph. |
| `name` | No | Visible in `get_episodes` listings | Thread ID, task label, status, next-step — orientation metadata for future agents. |
| `source_description` | No (in text mode) | BM25 full-text index | Keyword-rich summary for retrieval. Under 200 chars. |

### Contrast: useful vs. noisy episode_body

Noisy — abstract nouns produce ungreppable, unclassifiable nodes:
> `RAG retrieval includes hybrid scoring and brain-recall re-ranking.`
> → nodes: "RAG retrieval", "hybrid scoring", "brain-recall re-ranking" — none greppable, none classifiable as Anchor, INCLUDES is vague

Useful — concrete anchors produce navigable graph structure:
> `` `Retriever.ts` calls `brainRecallCache.process` after scoring. ``
> → nodes: "Retriever.ts" (Anchor ✓), "brainRecallCache.process" (Anchor ✓) — both greppable, edge CALLS is specific

---

## Step 1 — Think out loud (memory-draft)

Before calling `add_memory`, output a `memory-draft` block in chat. This block has two jobs: (1) draft your facts, and (2) catch problems before they hit the graph.

For each fact line, annotate what the backend will do with it. **If you spot a problem, fix it right there** — cross out the bad version and write the corrected one.

Template:

```memory-draft
What: <1 sentence: what the task accomplished + current state; note if AGENTS.md was updated>

Facts (draft each line, then check it):
- <SVO line>
  → nodes: [<predicted extracted nodes>] — greppable? classifiable? will deduplicate?
  → edge: <predicted relation_type> — specific enough?
- <SVO line>
  → ...
- ...

Proof: <what was verified and how> → <pass|fail|pending> (<detail>)
  → Where does this go? (see "Handling proof/status lines" below)

Next: <one imperative action with a concrete anchor, or "none">
```

### Fact-writing principles

Each line in your draft programs graph structure. Apply these:

- **One atomic subject-verb-object per line.** "reads and updates" → split into two lines with distinct verbs. Compound predicates create merged edges like READS_AND_UPDATES that lose precision.
- **Subjects and objects must be greppable.** File paths, function/class/variable names, CLI commands, CSS selectors, URLs, table names. Ask: "Can I grep for this in a codebase?" If no, it will become an unclassifiable orphan node.
- **Verbs must distinguish relationship types.** Good: `calls`, `creates`, `reads`, `updates`, `loads`, `starts`, `initializes`, `depends_on`, `fixes`, `causes`, `exposes`, `implements`. Bad: `has`, `includes`, `relates_to`, `involves` — these produce vague edges that carry no navigational value.
- **Avoid abstract nouns as subjects or objects.** "Memory persistence", "scoring pipeline", "error handling" — none of these are greppable, none will deduplicate across episodes, and they won't classify as any useful entity type.
- **Target 3–7 fact lines.** Only deltas — new or corrected information relative to existing memory.

### Handling proof/status lines

Proof and verification results (e.g., `` `npm run lint` → fail ``) are single-entity status descriptions, not two-entity relationships. The backend may extract the command as a node but has no second entity to form a useful edge.

**Rule:** Put proof details in `name` (for orientation) and `source_description` (for retrieval). Only put a proof line in `episode_body` if you can write it as a genuine two-entity SVO — for example:
- `` `pytest tests/test_auth.py` validates `TokenRefresh.handle` `` → two anchors, VALIDATES edge ✓
- `` `npm run lint` fails with 83612 errors `` → one anchor, no second entity ✗ → keep in name/source_description only

### Handling non-Anchor scenarios (decisions, issues, verification)

Not every episode is about code structure mapping. When recording decisions, issues, or verification results, the greppability rule still applies but the anchors shift:

- **Issue**: use the error message key phrase or issue tracker ID as the entity name, not a description. `"CORS preflight 403 on /api/chat"` is greppable; `"authentication problem"` is not.
- **Decision**: frame as two anchors connected by a choice verb. `` `zustand` chosen over `redux` for state management `` → nodes: zustand, redux → edge: CHOSEN_OVER.
- **Verification**: the test/command name is one anchor, the thing it validates is the other. `` `pytest test_token.py` validates `TokenRefresh.handle` `` → VALIDATES edge.
- **WorkItem**: the imperative action itself. `` inspect `HybridScorer.ts` for scoring merge path `` → goes in the Next line, which maps to episode_body only if not "none".

---

## Step 2 — Map to `add_memory` fields

After your draft is reviewed and corrected, map to the three parameters:

| Draft section | → parameter | Format |
|---|---|---|
| Thread + What + Proof status + Next | → `name` | `thread:<project>_<topic> :: <short task label> \| <status>` |
| What + key anchors + proof one-liner | → `source_description` | Keyword-rich, under 200 chars. |
| Final corrected SVO lines only | → `episode_body` | One SVO per line. If Next is not "none", append: `Next action: <imperative with anchor>`. **Nothing else — no labels, no headers, no prose, no proof status lines.** |

---

## Example — architecture mapping (Anchor-heavy)

### Bad (no thinking, prose in episode_body)
```
name: Engram architecture inventory
episode_body:
Aim: Capture Engram's integration anchors.
Now: RAG retrieval includes hybrid scoring and brain-recall re-ranking. Memory persistence is handled by Dexie and zustand.
Facts:
- Retriever.ts applies brainRecallCache.process
- BrainRecallCache.ts maintains shortTermMemory
Check: npm run lint -> fail (83612 errors)
Next: Inspect HybridScorer.ts
source_description: ""
```
Problems: Aim/Now/Check/Next prose inside episode_body → backend extracts "RAG retrieval", "Memory persistence", "hybrid scoring" as noise nodes. Proof line creates orphan. No thinking visible.

### Good (thinking catches and fixes a mistake)

Chat output:
````
```memory-draft
What: Mapped Engram RAG pipeline anchors and cache layer; AGENTS.md updated with routing map.

Facts (draft each line, then check it):
- `Retriever.ts` calls `brainRecallCache.process` after scoring
  → nodes: [Retriever.ts, brainRecallCache.process] — both greppable anchors ✓
  → edge: CALLS — specific ✓

- `BrainRecallCache.ts` maintains `shortTermMemory` slots with decay
  → nodes: [BrainRecallCache.ts, shortTermMemory] — both greppable ✓
  → edge: MAINTAINS — specific ✓

- `memoryStore.ts` uses level-based visibility from `MemoryLevel` enum
  → nodes: [memoryStore.ts, MemoryLevel] — both greppable ✓
  → edge: USES — too vague, could mean anything
  ✗ rewrite: `memoryStore.ts` reads visibility levels from `MemoryLevel`
  → edge: READS — better, distinguishes from writes ✓

- `db.ts` creates per-chat Dexie databases named `Engram_{chatId}`
  → nodes: [db.ts, Engram_{chatId}] — both greppable ✓
  → edge: CREATES — specific ✓

- `ChatManager.ts` reads `scope_state` in the Dexie meta table
  → nodes: [ChatManager.ts, scope_state] — both greppable ✓
  → edge: READS — specific ✓

- `ChatManager.ts` updates `scope_state` in the Dexie meta table
  → nodes: [ChatManager.ts, scope_state] — will deduplicate with line above ✓
  → edge: UPDATES — distinct from READS ✓

Proof: `npm run lint` → fail (83612 indentation errors, exit 1), not Engram-specific
  → Single-entity status, no second anchor → name/source_description only, not episode_body.

Next: inspect `HybridScorer.ts` for the scoring merge path
```
````

Then `add_memory` call:
```
name: thread:sillytavern_engram_rag :: RAG pipeline + cache mapping | check:fail | next:HybridScorer.ts
source_description: Engram RAG pipeline: Retriever.ts, BrainRecallCache.ts, memoryStore.ts, db.ts, ChatManager.ts. Lint fail (83612 errors).
episode_body:
`Retriever.ts` calls `brainRecallCache.process` after scoring.
`BrainRecallCache.ts` maintains `shortTermMemory` slots with decay.
`memoryStore.ts` reads visibility levels from `MemoryLevel`.
`db.ts` creates per-chat Dexie databases named `Engram_{chatId}`.
`ChatManager.ts` reads `scope_state` in the Dexie meta table.
`ChatManager.ts` updates `scope_state` in the Dexie meta table.
Next action: inspect `HybridScorer.ts` for the scoring merge path.
```
