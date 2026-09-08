# CVBuilder V2 Handover Protocol

**Status:** Canonical, tool-neutral
**Date:** 2026-09-08

This document defines how work is safely handed off between coding agents on this repository — whether that's Claude Code to Codex, Codex to Claude Code, one Claude Code session to another, or any future tool. It exists because a new agent cannot see a prior agent's conversation; the repository itself must carry everything the next agent needs. See `docs/DECISIONS.md` V2-D035 and `AGENTS.md` for the standing rule this implements.

There are two supported handover modes.

## A. Clean Milestone Handover

Use this when a milestone (or a well-defined chunk of one) is genuinely complete and you are stopping deliberately.

### Preferred sequence

```
implementation complete
  → focused tests
  → full/milestone acceptance tests
  → docs/CURRENT_STATE.md update
  → docs/REQUIREMENT_TRACEABILITY.md update
  → git diff review
  → commit (only if authorized)
  → next agent independently verifies
```

Do not skip the "next agent independently verifies" step by assuming your own verification is sufficient — the point of this protocol is that the incoming agent re-derives confidence from the repository, not from trusting your report unverified. Your report exists to make that re-derivation fast, not to replace it.

### What the outgoing agent records

At minimum, in `docs/CURRENT_STATE.md` (see its structure) and/or the session's final report:

- **Branch** — which branch this work is on.
- **HEAD** — the commit hash after your final commit (or "uncommitted" if you did not commit, with an explanation why).
- **Milestone/task** — which milestone or task this work was authorized under.
- **Completed work** — what was actually built/changed, described concretely enough that someone could verify it without you.
- **Files/components changed** — the actual file list, not a paraphrase.
- **Migrations** — whether any were created, and their names.
- **Tests run and results** — the exact command(s) and what they reported (pass/fail counts, not just "tests pass").
- **Known issues** — anything you know is incomplete, wrong, or fragile, stated plainly. Do not omit a known issue to make the handover look cleaner.
- **Remaining work** — what is left in this milestone, if anything.
- **Next recommended action** — the single next concrete step.
- **Important decisions made** — anything that should also exist in `docs/DECISIONS.md`; confirm it is actually there, not just mentioned in your report.
- **Whether the working tree is clean** — `git status --short` output, or an explicit statement of what is intentionally left uncommitted and why.

## B. Emergency / Mid-Task Handover

This matters when a session ends unexpectedly — token exhaustion, an interrupted session, a dropped connection, or simply stopping mid-task without having reached a clean milestone boundary.

**The incoming agent must NOT immediately continue coding.** Reconstruct the actual state first.

### Required first steps

Run, in order:

```
git branch --show-current
git log -5 --oneline --decorate
git status --short
git diff --stat
git diff
```

Then:

1. Read `docs/CURRENT_STATE.md` and whichever other canonical docs (`requirements.md`, `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md`) are relevant to the area with uncommitted or recent changes.
2. Inspect the actual changed files, not just the diff summary — read enough of each changed file to understand what state it's in.
3. Determine what appears complete versus incomplete. Look for half-finished functions, TODO markers, commented-out code, or a mismatch between what a docstring/comment claims and what the code does.
4. Run the relevant deterministic tests and see what actually passes and fails right now — do not assume the prior agent's last reported test result still holds if any file has changed since.
5. Compare the actual observed state against `docs/CURRENT_STATE.md`'s documented intent. Note every place they disagree.
6. Report the reconstructed state explicitly — to the user/product owner, or in your own working notes if this is a background/autonomous session — before writing or changing any more code.
7. Only then continue implementation, and only within the currently authorized milestone/task scope (see `AGENTS.md` #6).

### Explicit standing rule

**Uncommitted work is not assumed to be correct, complete, or disposable.**

The incoming agent must preserve useful partial work unless it can either (a) prove through inspection and testing that the work is incorrect, or (b) obtain explicit authorization to discard it. "It looked unfinished" is not sufficient justification to delete someone else's — or your own prior session's — uncommitted changes. If you are unsure whether partial work is worth keeping, the safe default is to preserve it (commit it, stash it, or otherwise make it recoverable) and ask, rather than discard it and continue past that point.

This mirrors the general git-safety posture in `docs/ENGINEERING_RULES.md` §C: never discard another agent's uncommitted work blindly, and never use destructive git operations without explicit authorization for that specific action.
