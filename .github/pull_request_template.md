## Summary

<!-- What user or contributor outcome does this pull request deliver? -->

## Related issue

<!-- Use "Closes #123" when appropriate. -->

## Scope

<!-- List the important changes and explicit non-goals. Keep the PR focused. -->

## Type of change

- [ ] Bug fix
- [ ] Phase 1-11 foundation feature
- [ ] Experimental provider, voice, desktop, browser, integration, plugin, notification, or GUI change
- [ ] New or changed skill
- [ ] Refactor with no intended behavior change
- [ ] Tests
- [ ] Documentation or community files
- [ ] Build or maintenance

## Behavior and platform support

<!-- Include example commands/output and identify Windows, Linux, or macOS limitations. -->

## Safety and privacy

<!-- What data or system resources can this change read or affect? Which validation, allowlist, permission, or confirmation boundary applies? Use "No change" with an explanation when appropriate. -->

- Anticipated category: `READ` / `ACTION` / `SENSITIVE` / `DESTRUCTIVE` / not applicable
- [ ] User-controlled text is never executed as an arbitrary shell command.
- [ ] Tests do not perform unintended real-world actions.
- [ ] Logs, fixtures, and screenshots contain no secrets or personal data.

## Verification

<!-- List exact commands and results. -->

```text
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

## Checklist

- [ ] I reviewed the diff and kept it limited to the stated purpose.
- [ ] I added or updated tests for behavior changes.
- [ ] I added type hints and useful docstrings where appropriate.
- [ ] I updated user or contributor documentation where needed.
- [ ] Platform-specific behavior is isolated and safely tested.
- [ ] New runtime dependencies are justified in the description.
- [ ] Documentation labels behavior accurately as implemented, experimental, or planned.
- [ ] Optional-runtime verification is reported separately from fake-based unit coverage.
- [ ] This change does not silently implement a Phase 12+ planned capability.
- [ ] I am ready to follow the [Code of Conduct](../CODE_OF_CONDUCT.md).
