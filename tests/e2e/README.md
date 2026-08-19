# Browser e2e (local only, not in CI)

Need a real Chromium against the live UI server (`mira ui` on :8091).

    npm install playwright-core   # once, in this directory
    node newui.test.mjs           # Svelte UI: picker unclipped, waiting timer
                                  # survives chat switch, media resume keeps
                                  # startedAt and shows live download progress

Each run prints PASS/FAIL per check and exits non-zero on failure.
