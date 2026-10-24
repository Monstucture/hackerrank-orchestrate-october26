# Spawn prompt (paste to Claude Code team lead)

Enable agent teams (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`), then paste:

```text
Spawn an agent team with exactly 3 teammates using these subagent types:
- pm
- engineer
- qa

Do not spawn extra teammates. I am the human lead. You coordinate only.

Product: Ghost Data Still Exists — AI deletion-audit agent for Northstar Shop.
Contract: problem_statement.md and AGENTS.md §6. Dataset under dataset/ only.
Output: repo-root output.csv, 518 rows, plus code/evaluation reports.

Workflow (every slice):
PHASE A — PM + Engineer agree one slice. QA idle. No code yet.
PHASE B — Engineer implements. Messages PM with usage + gaps.
PHASE C — PM one refine, then updates FEATURE.md (name, what, usage steps, acceptance, out of scope).
PHASE D — PM messages QA. QA tests. PM triages. Blockers go to Engineer.

In slice 3, Engineer iterates against the sample harness without PM/QA round trips; phases C–D run once, when the slice closes.
Teammates do not spawn sub-agents. Each teammate logs its own turns to root log.txt (AGENTS.md §5.3).

Slice order (4 slices) is in FEATURE.md. Start PHASE A on slice 1 (intent gate + schema writer).
After each phase, the owner messages the next owner. Do not wait for me unless blocked on a product decision.
```
