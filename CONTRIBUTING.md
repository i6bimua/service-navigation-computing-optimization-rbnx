# Contributing

Contributions are welcome when they preserve the project's evidence-based
support and benchmark boundaries.

## Development Setup

```bash
conda create -n robonix-compute python=3.10 -y
conda activate robonix-compute
python -m pip install -e ".[dev,websocket]"
```

## Before Opening a Pull Request

```bash
python -m pytest -q
python scripts/check_docs.py
python scripts/release_audit.py
python -m build
```

User-visible changes must include the corresponding documentation and test
evidence.

## Model Support

A new model is not “supported” because it can be imported. Include:

1. model-specific S1/S2 adapters;
2. payload serialization tests;
3. a lightweight contract smoke test;
4. complete checkpoint and launch instructions;
5. benchmark metadata and result provenance;
6. English and Chinese README updates.

## Benchmark Changes

- Do not overwrite an existing result with a different hardware or split.
- Record model revision, episode count, hardware, power mode, latency
  definition, and network settings.
- Separate full benchmark values, reproduced subset values, and smoke-test values.
- Do not use single-episode or local two-GPU smoke results as headline evidence.
- Regenerate the benchmark SVG from the committed CSV:

```bash
python benchmarks/r2r_ce/render_results.py
```

## Code Style

- Keep the runtime dependency surface small.
- Avoid importing heavyweight model packages at module import time.
- Preserve external InternNav identifiers only at the adapter boundary.
- Do not add model weights, datasets, private paths, credentials, or raw large
  outputs to Git.

## Pull Request Description

Describe:

- the user-facing problem;
- the implementation boundary;
- commands used for validation;
- performance evidence for runtime-affecting changes;
- known hardware or dataset requirements.
