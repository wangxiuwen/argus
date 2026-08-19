# Browser e2e (local only, not in CI)

These need a real Chromium against the live UI server (`mira ui` on :8091).

    npm install playwright-core   # once, in this directory
    node picker.test.mjs          # model menu opens unclipped
    node resume.test.mjs          # media resume keeps startedAt + waits for download
    node waiting.test.mjs         # agent waiting timer survives a chat switch

Each script prints PASS/FAIL and writes screenshots to out_*.png.
