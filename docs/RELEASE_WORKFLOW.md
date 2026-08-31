# Release Workflow

## This Repo's Role in the Release

ember-2 is the backend. It versions independently but must be pinned by ember-2-installer at release time. The installer workflow downloads a specific tagged version of this repo to bundle into the installer.

## What to Do Before Cutting a Release

1. Ensure all tests pass: pytest tests/
2. Run retrieval eval: python tools/eval_retrieval.py -- no regression
3. Bump version in version.json
4. Update CHANGELOG.md
5. Commit, tag, and push: git tag vX.X.X && git push origin vX.X.X
6. Publish a GitHub Release at that tag -- the installer workflow needs a published release to reference
7. Note which ember-2-ui version this backend is compatible with -- document in release notes

## Automated coordination (shipped — Release Please active across all three repos)

Release Please runs on `ember-2`, `ember-2-ui`, and `ember-2-installer`. Conventional Commits drive changelog generation and version bumps. Release-please opens a release PR on each repo; a human merges the PR to cut the release. The cross-repo build coordination still requires the manual checklist above (version pinning, ember-2-ui tag selection, installer bundling) — Release Please handles per-repo release mechanics, not cross-repo orchestration.

## RELEASE_PLEASE_TOKEN (required secret)

### Why it exists

GitHub deliberately does not create workflow runs from events raised by the
default `GITHUB_TOKEN`. `googleapis/release-please-action@v4` defaults to
`token: ${{ github.token }}`, so every event release-please raises -- the
release PR it opens, the tag it pushes, the GitHub Release it publishes -- is a
dead end. Two gates were silently skipped as a result:

- **pytest gate (ember-2)** -- `.github/workflows/tests.yml` runs on
  `pull_request` into `main`. The release PR is opened by `github-actions[bot]`,
  so no `pull_request` event fires and the gate never runs on the one PR that
  ships to users. Confirmed on the v0.18.0 release PR: zero checks.
- **artifact workflow (ember-2-installer)** -- `.github/workflows/release.yml`
  runs on `release: [published]`. release-please publishes the release, so no
  `release` event fires and the Windows / macOS / Linux installers are never
  built. The v0.18.0 installer artifacts existed only because a human
  republished the release by hand.

Passing an explicit PAT attributes those events to a real user, which does fire
workflows.

### Scope

`RELEASE_PLEASE_TOKEN` must be set as a repository secret in **all three**
repos, because each runs its own release-please:

- `niansahc/ember-2`
- `niansahc/ember-2-ui`
- `niansahc/ember-2-installer`

One token, added three times. GitHub rejects secret names beginning with
`GITHUB_`, which is why the name is not `GITHUB_PAT`.

### Token type and permissions

Fine-grained personal access token. GitHub Settings -> Developer settings ->
Personal access tokens -> Fine-grained tokens.

| Field | Value |
|---|---|
| Resource owner | `niansahc` |
| Repository access | Only select repositories: `ember-2`, `ember-2-ui`, `ember-2-installer` |
| Contents | Read and write (commits, branches, tags, releases) |
| Pull requests | Read and write (open and update the release PR) |
| Metadata | Read-only (mandatory, auto-selected) |

Nothing else. Do not grant Workflows, Actions, Administration, or Secrets.

`Workflows: Read and write` would only be needed if a release commit ever
modified a file under `.github/workflows/`. The `extra-files` entry in
`release-please-config.json` is `version.json` (plus `CHANGELOG.md`), so it is
not needed today. If that changes, the failure is explicit: `refusing to allow a
Personal Access Token to create or update workflow ... without workflow scope`.

### Expiry and renewal

Fine-grained PATs cap at 1 year. Created 2026-08-01, so it expires
**2027-08-01**. Set a renewal reminder for **2027-07-24**, one week ahead.

On expiry the `Release Please` job fails with `401 Bad credentials`. No release
PR is opened or updated until the secret is replaced in all three repos.

### Fallback behavior if the secret is missing

There is deliberately no `|| github.token` fallback. If `RELEASE_PLEASE_TOKEN`
is unset, the expression resolves to an empty string, which explicitly
overrides the action's default, and the `Release Please` job fails with a
credentials error on the next push to `main`.

This is intentional. Silent degradation back to `GITHUB_TOKEN` is exactly the
failure mode that hid this bug across several releases. A red job is the point.

Nothing is lost when it fails: no commits, no tags, no partial release. Add the
secret and re-run the job, or push any commit to `main`, and release-please
picks up from the same place.

**Sequencing:** add the secret to all three repos *before* merging the workflow
change. If the change merges first, `Release Please` stays red until the secret
exists.

## release-please Auto-Merge Prohibition

Release-please PRs (title format: `chore(main): release X.Y.Z`) must NEVER have auto-merge enabled. These PRs require explicit human approval and manual merge only. All other PR types may use auto-merge as normal.

Rationale: a release is a deliberate human decision. Allowing release-please PRs to auto-merge bypasses the human gate that decides when a release is cut. v0.17.0 was auto-released without explicit approval — that was the trigger for codifying this rule. Same wording exists in CLAUDE.md and `.claude/commands/pre-release.md` so the rule is reinforced at the policy, ops, and workflow layers.
