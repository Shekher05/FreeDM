# Epic: myIDM — personal segmented download manager

Build-to-learn project. Full context in [`PROJECT.md`](../../PROJECT.md),
requirements in [`.claude/prds/personal-download-manager.prd.md`](../../.claude/prds/personal-download-manager.prd.md).

## Milestones

- [ ] **M1 — Segmented engine + CLI:** `python -m myidm <url>` gives a fast, resumable download with live progress
- [ ] **M2 — Local background service:** engine runs in the background, accepts URLs over a `127.0.0.1` endpoint, persistent queue, status
- [ ] **M3 — Chrome extension:** right-click a link in Chrome to send it to the service; popup shows progress

## Milestone 1 tasks

Plan: [`docs/superpowers/plans/2026-09-07-myidm-segmented-engine.md`](../superpowers/plans/2026-09-07-myidm-segmented-engine.md)

- [ ] 1. Project skeleton + `split_ranges` (byte-range splitting, pure function)
- [ ] 2. `derive_filename` (Content-Disposition / URL / fallback, path-traversal stripping)
- [ ] 3. Resume sidecar load/save helpers (`<name>.myidm.json` contract)
- [ ] 4. Local range-capable HTTP test-server fixture (`tests/conftest.py`)
- [ ] 5. Segment download + parallel orchestration (fresh download, range-capable servers)
- [ ] 6. Resume (prove only-missing-bytes are fetched)
- [ ] 7. Single-connection fallback (servers without range support)
- [ ] 8. CLI (`python -m myidm`, argparse + progress line)
- [ ] 9. README + manual smoke test

## Definition of done (M1)

- `python -m pytest -q` green, `ruff check .` clean
- Real download resumes correctly after Ctrl-C, checksum stable
- Fallback path works against a server with no range support
