# New-conversation handoff prompt

Copy the prompt below into a new Codex conversation opened in this repository:

```text
Continue the Trace Temporal AI hackathon project in this repository. Read AGENTS.md
first and follow it as the source of truth. Then run `entire doctor`, inspect
`git status --short`, fetch `origin/main`, and read the component READMEs relevant to
the next task. Preserve all teammate work and do not add unrelated local tool folders.

Before editing, briefly explain the product problem, the current end-to-end flow, and
which challenge requirement the proposed work advances. Confirm the actual state from
the repository rather than relying only on this prompt.

Unless the repository now shows that it is already complete, take the highest-leverage
next step: create a fair, reproducible held-out baseline comparison against the deployed
OpenTSLM model, using the same recording-grouped test split and clearly separating
measured facts, publisher annotations, and generated predictions. Produce the evaluation
artifact needed for the submission and connect it to the demo or documentation where
useful. After that, verify the TimeNet connector path and improve the event explanation
flow only where evidence shows it is unclear.

Run the appropriate tests, inspect the result, and continue until the chosen step is
complete. Keep the scope replay-only and retrospective. Do not add live telemetry,
robot control, an extra orchestration LLM, or claimed exact 3D motion. If UI work is
needed, use the Impeccable skill and verify it in the browser. Commit only intended
files with Entire active, sync with current main, and push to main when verification
passes; existing authorization to push this project persists.

At the end, tell me in simple language what changed, why it helps the challenge, the
evidence that it works, what remains, and the next highest-priority action.
```

If a different priority is desired, replace the third paragraph with the concrete task
while keeping the startup, verification, scope, and handoff instructions.
