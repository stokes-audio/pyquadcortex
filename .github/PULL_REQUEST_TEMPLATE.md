## Summary

<!-- What does this change do, and why? -->

## Related issue

<!-- e.g. Closes #123 -->

## Testing

<!-- Offline: `python -m pytest -q` and mypy run in CI. Say what else you ran. -->

## Hardware

<!-- Every pull request runs the hardware suite before it is marked ready; see
     contributing.md, "Before you mark a pull request ready". Fill in ONE of:
     - Run: `pytest tests/hardware --hardware` on commit <short hash>, CorOS <version>.
       Paste the `operations on ...` block and pytest's last line. If the change
       adds or alters an operation, say how you verified it (read-back, or the screen).
     - No unit: say so and mark ready anyway; a maintainer runs the suite before merging.
     - Waived by a maintainer, with the reason (a change that cannot reach the wire). -->

## Checklist

- [ ] Opened as a draft; the Hardware section above is filled in before marking ready
- [ ] Tests pass locally (`python -m pytest -q`)
- [ ] Added or updated tests for the change
- [ ] Updated documentation if behavior or the public API changed
- [ ] No unrelated changes bundled in
