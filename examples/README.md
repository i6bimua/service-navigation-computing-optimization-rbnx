# Examples

## In-Process Mock

```bash
python examples/mock_compute.py --steps 5
```

No model or GPU is required.

## HTTP Skill with Mock Runtime

```bash
robonix-compute-skill \
  --config-json examples/robonix_compute_config.json
```

## HTTP Skill with InternNav

Set the cloud host, model paths, and device in
`robonix_compute_internnav_config.json`, then:

```bash
robonix-compute-skill \
  --config-json examples/robonix_compute_internnav_config.json
```

The real path requires a running `robonix-compute-cloud` service and an
installed InternNav environment.
