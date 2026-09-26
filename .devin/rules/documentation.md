---
description: "Documentation policy — AGENTS.md is read-only"
trigger: always_on
---

Do NOT create, append to, or edit `AGENTS.md`. It is a hand-curated
minimal file; session findings, feature documentation, debugging notes
and Blender API gotchas must go in standalone docs:

- `doc/engineering/<topic>.md` for implementation notes/gotchas
  (update `doc/engineering/README.md`)
- `doc/<topic>.md` for adapter-level docs
- workspace `dev-tools/SESSION_LOG.md` for per-session findings
