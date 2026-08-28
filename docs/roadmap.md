# Implementation Roadmap

## Phase 1: Public contract

- Freeze input, output, exit-code, security, and non-goal contracts.
- Keep current detector behavior as the compatibility baseline.
- Add deterministic fixtures for every behavior described as supported.

## Phase 2: Runtime adapters

- Keep the core detector dependency-free and framework-neutral.
- Normalize common text and tool-call event shapes before detection.
- Add small adapters instead of coupling the core to one Agent framework.

## Phase 3: Policy integration

- Keep detection and action selection separate.
- Require idempotency metadata before approving side-effect retries.
- Make stop, retry, fallback, and handoff decisions observable.

## Phase 4: Evaluation and release quality

- Add threshold-sensitivity and false-positive fixtures.
- Benchmark counts and latency without claiming universal accuracy.
- Run syntax, behavior, link, secret, and packaging checks in CI.
- Publish only after the final commit's CI run is green.

## Deferred

- Distributed tracing and dashboards.
- Provider-specific diagnosis.
- Automatic process termination or provider switching.
- Persistent storage of raw sessions.
