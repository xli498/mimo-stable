# Contributing

Thanks for improving `mimo-stable`.

This repository deliberately keeps zero runtime dependencies. Keep contributions
small, standard-library based, and runnable with Python 3.10+ unless there is a
clear reason to change that boundary.

## Before opening a pull request

1. Keep changes focused and explain the user-visible effect.
2. Do not commit API keys, tokens, private logs, or personal data.
3. Run the repository checks locally when they exist.
4. Update documentation when behavior, commands, or constraints change.
5. Run the checks before requesting review:

   ```bash
   bash scripts/test_short.sh
   bash scripts/test_long.sh
   ```

## Detection and safety boundaries

- Fixtures must be synthetic or fully redacted. Never commit API keys, private
  prompts, user data, raw production tool payloads, or private endpoints.
- A detector may emit signals only. Do not add implicit retries, model switches,
  tool calls, or other side effects to detection code.
- Recovery policy may recommend an action, but execution remains the caller's
  responsibility.
- Do not turn an observation from one model, endpoint, or task into a universal
  root-cause or parameter-effectiveness claim. State evidence and uncertainty.

## Issues

Use the issue templates for reproducible bugs or concrete improvement proposals. Include expected behavior, actual behavior, and the smallest useful reproduction.
