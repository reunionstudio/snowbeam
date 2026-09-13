# GitHub Actions setup

These files are templates, not enabled workflows. The current publishing token
permits repository and release publication but lacks the `workflow` scope needed
to add files under `.github/workflows`. The first public alpha is built and
verified locally using the [release procedure](../releasing.md).

When workflow-capable GitHub authorization is available, copy `ci.yml`,
`homebrew.yml`, and `publish.yml` from this directory into `.github/workflows/`
and commit them. Their reusable-workflow paths already target that directory.

- **Checks:** Linux/macOS on Python 3.11, 3.13, and 3.14, plus Homebrew packaging.
- **Homebrew:** install and test the generated formula with declared dependencies
  on a clean macOS runner, then remove the test installation.
- **Publish:** verify the release tag, rerun checks, build the distributions,
  and attach the same archives, checksums, and formula to the GitHub release.
- **Optional PyPI:** only runs when `PYPI_PUBLISH_ENABLED=true`; configure Trusted
  Publishing and a protected `pypi` environment before enabling it.

For the first automated release, require a successful Checks run on the exact
commit selected for the release. Do not confuse templates or successful local
tests with completed hosted CI.
