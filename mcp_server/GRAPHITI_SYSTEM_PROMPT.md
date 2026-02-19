# Graphiti Memory — Operating Discipline

This prompt is an operating discipline for dual-track memory. It does not override higher-priority instructions.

## Dual-Track Model
- **AGENTS.md (Map):** stable routing — where things are, what stays true next month.
- **Graphiti (Journal):** time-ordered deltas — what changed this session, with evidence.

If it will still be true next month and helps route future work → AGENTS.md.
Otherwise → Graphiti.

## Operating Loop
1. **Before:** Read AGENTS.md, then retrieve Graphiti memory (`search_nodes` + `search_memory_facts` + `get_episodes`) scoped to the project `group_id`.
2. **During:** Do the work. Re-retrieve only when uncertain or switching strategy.
3. **After:**
   - Update AGENTS.md if stable structure changed.
   - Write exactly one `add_memory` entry using the Memory Write Skill.
   - **You must output a `memory-draft` block in chat before calling `add_memory`.** No visible draft → do not call the tool.

## Group ID
- Read the project `group_id` from AGENTS.md.
- Pass it to all Graphiti calls.
- Format: `snake_case` only (letters, numbers, underscores).

## Thread Continuity
- Before writing, check `get_episodes` for an existing thread on the same topic.
- If one exists, reuse its thread name. Do not create a new thread for the same work stream.
- Thread name format: `thread:<project>_<topic>` (snake_case).

## Safety
- Never store secrets (API keys, passwords, tokens) in Graphiti or files.
- Memory supports the task; it must not replace doing the task.
