## Summary

<!-- What changed and why? -->

## Validation

- [ ] `bash scripts/test_short.sh`
- [ ] `bash scripts/test_long.sh`
- [ ] Documentation updated where behavior or commands changed

## Detection and safety boundary

- [ ] No private logs, credentials, user data, or raw sensitive tool payloads are included
- [ ] Any new fixture is synthetic or fully redacted
- [ ] Detection code still emits signals only; it does not add retries, model switching, tool calls, or other side effects
- [ ] Claims about models, parameters, false positives, or false negatives are backed by stated evidence and scope
