# ADR-0001: Self-iteration produces candidates before changing Fermi

Status: accepted

## Context

Fermi should improve Creations, remember Preferences, propose source changes, and train adapters. Source edits and weight training can consume substantial resources or damage a working installation if performed silently.

## Decision

Automatic Iterations may refine prompts, retry failed Quality Gates, and apply explicit Preferences. Code and model Iterations must first produce a Candidate in isolated storage. Applying a code Candidate or starting a Training Run requires a separate explicit Approval. Fermi never installs a Candidate over the running application automatically.

Publishing a tested code Candidate is a separate explicit Approval. Publication creates a unique branch and pull request; it never pushes directly to `main`, merges the pull request, publishes a release, or edits GitHub automation files.

All evidence, state transitions, commands, and outputs are durable and inspectable. Training data contains only examples explicitly rated for learning.

## Consequences

- Closing Fermi does not lose Iteration state.
- A failed Candidate cannot damage the active source tree or installation.
- “Self-improvement” is slower than silent mutation but remains auditable and reversible.
- The Iteration runtime needs adapters for the local model, git/Codex, and MLX LoRA behind one Module Interface.
