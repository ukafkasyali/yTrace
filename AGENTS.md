# Agent Setup Checks

Before doing repository work in Codex, verify that Entire is active for the current worktree:

```bash
entire doctor
```

The expected Codex-related output is:

```text
✓ Codex hooks: ACTIVE
✓ Codex hook approval records: PRESENT
```

If `entire doctor` reports missing or untrusted Codex hooks, run:

```bash
entire enable --agent codex
```

Then open `/hooks` in Codex and approve the Entire hooks. Rerun `entire doctor` before continuing.
