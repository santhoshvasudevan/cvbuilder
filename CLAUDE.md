# CVBuilder V2 — Claude Code Instructions

This repository is developed on branch `cvbuild2`.

Claude Code must read and obey, in this order, before making any change:

1. **`AGENTS.md`** — the tool-neutral entry point for any coding agent: pre-work checklist, standing rules, and pointers to everything below.
2. **`docs/ENGINEERING_RULES.md`** — the canonical engineering agreement: sources-of-truth precedence, architectural invariants, and the Git/database/testing/documentation/secrets rules.
3. **`docs/HANDOVER_PROTOCOL.md`** — how to hand off to, or resume from, another agent (including a prior Claude Code session that ended unexpectedly).
4. **`docs/MILESTONE_COMPLETION_CHECKLIST.md`** — work through this before declaring any milestone complete.

These four documents are canonical and tool-neutral. **Do not duplicate their content here.** If a rule matters, it belongs in one of them, not restated in this file. This file adds only what is specific to using Claude Code on this repository.

## V2 product priority (one-line orientation)

The primary goal is expert-quality candidate positioning and persuasive relevance — the operator owns final factual approval; do not optimize the system into an overly conservative fact extractor. Full detail: `requirements.md` §1–2 and `docs/ENGINEERING_RULES.md` §B.

## Claude-Code-specific notes

- Confirm branch, HEAD, and `git status` (AGENTS.md #1) before using any file-editing tool, even mid-session — do not rely on this session's memory of a state you haven't just re-checked.
- Do not automatically commit unless the user explicitly asks, or the current task's instructions explicitly authorize it (`docs/ENGINEERING_RULES.md` §C).
- Never weaken or remove a test merely to make it pass (`docs/ENGINEERING_RULES.md` §E).
- If the working tree already has uncommitted changes from a prior session (this one's or another agent's) when you start, do not silently mix them into your own commit — inspect them, understand what they are, and report the situation before proceeding (`docs/HANDOVER_PROTOCOL.md` §B).
- When updating canonical docs, keep `docs/CURRENT_STATE.md` concise and operational — full history belongs in `docs/DECISIONS.md`, not restated there.
