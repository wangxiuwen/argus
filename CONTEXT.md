# Fermi domain language

- **Creation** — one requested image, song, or video and its prompt, output, quality evidence, and feedback.
- **Batch** — a durable ordered set of Creations requested by one Agent tool call.
- **Iteration** — one attempt to improve a Creation, a Preference, Fermi source code, or a model adapter from measured evidence.
- **Quality Gate** — deterministic checks plus a local-model critique that decide whether a Creation may finish or should be retried.
- **Preference** — durable, user-approved guidance that the Agent applies to later Creations. Inferred Preferences are never promoted without evidence.
- **Feedback** — an explicit rating and optional note attached to a Creation or conversation example.
- **Candidate** — an isolated code change or model adapter that has not been approved for use.
- **Approval** — an explicit user action allowing a Candidate to change the source tree or start a training run.
- **Training Run** — a durable MLX LoRA/QLoRA process built only from approved Feedback examples.
- **Publication** — an explicitly approved GitHub branch, commit, and pull request produced from a tested code Candidate. A Publication is never an automatic merge.
