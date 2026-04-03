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

## Planned: Automated Coordination (v0.14.0)

The installer repo will adopt Release Please and GitHub Actions to automate cross-repo builds. Until then, releases are manual per the checklist above.
