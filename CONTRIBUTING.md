# Contributing

## Commits

Use short, focused commits with one lowercase subject line:

```text
feat: compare earlier prompts
fix: preserve history boundaries
docs: clarify cache metric definitions
```

Use `<type>: <short summary>`, normally `feat`, `fix`, `docs`, `test`,
`refactor`, `perf`, `build`, `ci` or `chore`. Keep the entire subject lowercase,
prefer no scope, omit a trailing period, and keep it within 72 characters.
Leave the commit body empty: no description paragraphs or attribution trailers.
Each commit should contain one coherent change with its relevant checks.

Use the repository's existing configured user identity for both author and
committer. Check the effective identities with `git var GIT_AUTHOR_IDENT` and
`git var GIT_COMMITTER_IDENT` before committing; environment overrides can differ
from Git configuration. Ask if the user identity is missing rather than
inventing one. Do not change Git identity settings, attribute a commit to
an AI assistant, add AI co-author/generated-by trailers, or add an AI assistant
to contributor lists. The user has authorized focused commits at completed,
validated checkpoints; do not ask again for each such commit. Pushing and
publication require separate authorization.

## Code comments

Comments should let a reader understand a component's role and the reasoning
behind its behavior without needing the original conversation. Write them
alongside the code and update them when that behavior changes.

### What to explain

- Give each Go package and Python module a short description of its
  responsibility, where it sits in the data flow, and any important boundary.
- Document exported Go types/functions and nontrivial Python functions. Explain
  the contract: what the caller supplies, what it gets back, side effects,
  ownership/cleanup, and failure behavior where relevant.
- Put inline comments immediately before decisions that need context: locking
  and goroutine ownership, persistence ordering, cancellation, partial writes,
  protocol framing, or an intentional tradeoff.
- Define measurement terms at their observation point. Explain what a hash,
  timestamp, completion flag, or null value actually establishes.
- In tests, explain unusual fixtures and synchronization gates, particularly
  what an assertion proves and why a simulated failure is representative.

Use plain, concrete language and complete sentences. Start Go documentation
comments with the documented symbol's name; use Python docstrings for function
contracts. Keep comments close enough to the code that they can be checked.
Short helpers need only a short description when their names and types leave
the behavior clear. Longer contracts can use a second paragraph.

### What to leave out

Avoid line-by-line narration, decorative section banners, repeated signatures,
and comments that merely translate syntax into English. Prefer a clearer name
or smaller function when the code itself can express the idea. Do not add a
comment to every statement or trivial forwarding method.

Keep architecture walkthroughs, full schemas, commands and experiment protocols
in the README or docs; link to them when they provide necessary context. Do
not paste evolving design documents into function comments. Remove commented-out
code. A TODO should name a specific missing behavior or decision and its impact;
use an issue reference when one exists, without inventing one.

For example, above the body-storage limit:

```go
// Hash all observed bytes before limiting storage so a capped sidecar still
// identifies the full observed payload. Stored bytes have a separate hash.
```

For an offline reader:

```python
def body_bytes(root, record, name):
    """Validate a sidecar and return (complete_bytes, integrity_state).

    Incomplete captures return None so downstream analysis cannot treat a
    stored prefix as a complete request or response. Corruption raises TraceError.
    """
```

### Review checklist

Before finishing a change, check that:

- The component's responsibility and consequential choices are understandable.
- Public contracts describe relevant failure and cleanup behavior.
- Lock ownership, persistence order and protocol assumptions match the code.
- Comments distinguish observed facts from inferred measurements.
- Changed code has no stale comments or duplicate explanations.

This is a review convention. Comment-count or coverage thresholds do not measure
whether the explanations are useful.
