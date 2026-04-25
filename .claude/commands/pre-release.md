# Pre-Release Checklist

**Critical principle: CC runs the full release process. Nothing is "done" until it is publicly downloadable. Never assume the human is cutting the release unless they explicitly say so.**

A release is not complete at commit. A release is not complete at tag. A release is complete when:
- The GitHub Release is published (not draft)
- Artifacts are attached (installer .exe / source)
- latest.yml is present in release assets (installer only)
- The release is visible and downloadable at the GitHub Releases URL
- CC has verified the above and reported the URL

---

## Pre-release (run before every release)

### ember-2 (backend)
- [ ] All tests passing: pytest tests/
- [ ] Streaming SSE regression test passing: pytest tests/test_streaming_regression.py -v
- [ ] Retrieval eval passing: python tools/eval_retrieval.py — no regression
- [ ] Web search eval run: python tools/eval_web_search.py --auto-search — document trigger rate
- [ ] Conversation eval run: python tools/eval_conversations.py — document results (Tier 4, minor/major only)
- [ ] CHANGELOG.md updated (release-please handles this via conventional commits)
- [ ] version.json bumped (release-please handles this via conventional commits)
- [ ] All changes committed and pushed to main: git push origin main
- [ ] Constitution, nature, and Lodestone layers reviewed for coherence
- [ ] Deviation drift check — review deviation record distribution against nature document; verify accumulated character is consistent with intended character
- [ ] Research review: any watch items ready to graduate to roadmap?
- [ ] API restarted with all current code active before UAT
- [ ] UAT manual testing — run `python scripts/uat_runner.py`, all critical cases pass or documented exceptions approved by human. Results logged to `logs/uat_results_latest.json` and `logs/uat_results_history.json`.
- [ ] /security-review on G if anything touched vault write paths, API endpoints, or auth logic
- [ ] Any significant commits that didn't get /simplify — run it now

### ember-2-ui (frontend)
- [ ] All Playwright tests passing: npm run test:e2e
- [ ] CHANGELOG.md updated
- [ ] package.json version bumped
- [ ] All changes committed and pushed to main: git push origin main
- [ ] UI rebuilt from correct source: npm ci && npm run build
- [ ] Production UI bundle built and copied to ember-2/ui/ — `npm ci && npm run build`, confirm FastAPI is serving the new bundle

### ember-2-installer (installer)
- [ ] All Playwright tests passing
- [ ] CHANGELOG.md updated
- [ ] package.json version bumped
- [ ] All changes committed and pushed to main: git push origin main
- [ ] Frontend freshly built from pinned ember-2-ui tag before packaging
- [ ] Backend version pinned and documented in release notes
- [ ] Installer built: npm run dist
- [ ] app-update.yml present in dist/win-unpacked/resources/ — verify before publishing
- [ ] latest.yml will be attached to release by electron-builder — verify after publishing

---

## At this release boundary

- [ ] CLAUDE.md length audit on all three repos (targets: G under 400 lines, M under 150, Y under 120) — remove anything CC wouldn't miss
- [ ] Graduate any standout corrections from sessions into CLAUDE.md rules (8A habit — the self-improvement loop)

---

## Release (CC runs this, not the human)

- [ ] Git tag created: git tag vX.X.X
- [ ] Tag pushed: git push origin vX.X.X
- [ ] GitHub Release created (NOT draft): gh release create vX.X.X --title "vX.X.X" --notes "..." --latest
- [ ] Artifacts attached to release (installer .exe for yellow, source zip for green)
- [ ] Release verified as published and visible: gh release view vX.X.X
- [ ] Release URL reported to human: https://github.com/niansahc/[repo]/releases/tag/vX.X.X

## Post-release verification (CC runs this)

- [ ] Confirm release appears at https://github.com/niansahc/[repo]/releases
- [ ] Confirm latest.yml is present in release assets (installer only)
- [ ] Confirm version matches package.json / version.json
- [ ] Sync project knowledge files to Claude project after every release (Manager Claude depends on this for architecture sessions)
- [ ] TDD version bump at every release (current: 1.2; bump minor for feature releases, patch for hotfixes)
- [ ] Report to human: "Release vX.X.X is live at [URL]. Users can download/update now."

## Context layer change gates

- [ ] Context packet token estimate validated before shipping any context layer changes — must stay under 4,000-6,000 tokens at average turn
- [ ] Run retrieval eval before AND after any context packet order changes — confirm no regression before ship

## Patch releases

Patch releases follow the same checklist. There are no shortcuts for patches. A patch that is committed but not published is not a patch — it is unpublished work. Every patch must complete the full release process before being called done.

---

## Release Process

### Gates — mandatory before any release or patch is cut

**Documentation gate (all three repos):**
- [ ] CLAUDE.md version and test count current
- [ ] TDD updated to reflect what shipped (G only)
- [ ] README reflects current features
- [ ] CHANGELOG.md current (release-please handles via commits)

**Quality gate:**
- [ ] All tests passing
- [ ] Retrieval eval passing with no regression (G only)
- [ ] No flaky tests carried forward
- [ ] Manual eval battery — run tools/eval_manual.py with qwen3:8b and Haiku separately. Full 19-question battery, all 7 categories. Document results in docs/eval_history.md. This is the Tier 2 gate. Required before every major release.
- [ ] Automated eval suite — run pytest tests/eval/ -m eval --runs 3. Document results including GOLD-R-001 pass/fail. Minimum 3 runs before any result is treated as signal.

**Coordination gate:**
- [ ] All three repos confirm docs and tests green
- [ ] Human approves before any tag is created
- [ ] GitHub Release not created until human says go

### Sequence

1. G, M, Y each complete documentation and quality gates
2. Each reports green to manager
3. Manager confirms all three green and gets human approval
4. G coordinates the release — tags all three repos, creates GitHub Releases
5. Y attaches installer artifacts (.exe, latest.yml)
6. G verifies all three releases are publicly visible
7. G reports release URLs — release is not done until this step

### Y independent releases

Y may cut an installer-only release when:
- Changes are installer-specific only (no backend or UI updates)
- Human explicitly approves
- Y completes documentation and quality gates independently
- Y tags, creates GitHub Release, attaches artifacts, and reports URL

Y does NOT cut independent releases when backend or UI changes are involved — coordinate with G.

### release-please

All three repos use release-please for automated release PRs. Conventional commits are required. Release PRs require human approval before merging.

---

*Note: this checklist lives at `.claude/commands/pre-release.md`. Update it here, not in CLAUDE.md.*
