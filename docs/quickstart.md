# Quick Start and Deployment

## 1. In-Process Mock

Use the mock path to validate synchronization, timeout handling, context reuse,
and telemetry without a model or GPU:

```bash
bash scripts/run_mock_compute.sh --steps 5
```

The command writes `outputs/mock_compute/telemetry.json`.

## 2. WebSocket Mock

Start the cloud process:

```bash
robonix-compute-cloud \
  --mode mock \
  --host 0.0.0.0 \
  --port 8765
```

Start the edge process:

```bash
robonix-compute-edge \
  --mode mock \
  --cloud-host 127.0.0.1 \
  --cloud-port 8765 \
  --steps 5
```

## 3. Real Cloud S2 Service

```bash
conda activate robonix-compute

robonix-compute-cloud \
  --mode internnav \
  --host 0.0.0.0 \
  --port 8765 \
  --model-dir "$ROBONIX_COMPUTE_MODEL_DIR" \
  --device cuda:0
```

Allow only trusted edge hosts to reach the service port. The example transport
does not replace production authentication, encryption, or network policy.

## 4. Real Edge S1 Runtime

Copy the exported S1-only checkpoint to the Orin/Thor host:

```bash
robonix-compute-edge \
  --mode internnav \
  --cloud-host <cloud-ip> \
  --cloud-port 8765 \
  --model-dir "$ROBONIX_COMPUTE_MODEL_DIR" \
  --s1-model-dir "$ROBONIX_COMPUTE_S1_MODEL_DIR" \
  --device cuda:0 \
  --stdin-jsonl
```

The edge process reads one JSON observation per line:

```json
{
  "step_id": 0,
  "instruction": "go to the kitchen",
  "observation": {
    "rgb": [[[0, 0, 0]]],
    "depth": [[0.0]],
    "pose": [0.0, 0.0, 0.0]
  }
}
```

It returns the action and per-step telemetry as JSON.

## 5. HTTP Skill Boundary

```bash
robonix-compute-skill \
  --host 0.0.0.0 \
  --port 8090 \
  --config-json examples/robonix_compute_internnav_config.json
```

Check the service and submit a step:

```bash
curl http://127.0.0.1:8090/health

curl -X POST http://127.0.0.1:8090/step \
  -H 'Content-Type: application/json' \
  -d '{"observation":{"rgb":[1.0,0.0],"depth":[0.0]}}'
```

The HTTP service is the standalone Skill boundary. RoboNix core integration
remains external to this repository.

## 6. Habitat Evaluation

```bash
export ROBONIX_COMPUTE_HABITAT_EPISODES=1,2,3,4,5
export ROBONIX_COMPUTE_OUTPUT_DIR=outputs/habitat_eval_r2r_5eps
export ROBONIX_COMPUTE_CLOUD_GPU_ID=1
export ROBONIX_COMPUTE_EDGE_GPU_ID=0
export ROBONIX_COMPUTE_CLOUD_PORT=18765

bash scripts/run_habitat_eval.sh
```

Expected summary artifacts:

```text
outputs/habitat_eval_r2r_5eps/
├── cloud_eval_stdout.log
├── edge_stdout.log
├── edge_cloud_s2_summary.json
├── edge_cloud_s2_control_steps.csv
├── edge_cloud_s2_episode_summary.csv
└── eval/
    ├── progress.json
    ├── result.json
    └── analysis_logs/
```

The `edge_cloud_s2_*` names are emitted by the external InternNav evaluation
script and are preserved at that compatibility boundary.

Inject synthetic round-trip delay:

```bash
export ROBONIX_COMPUTE_RTT_DELAY_MS=200
bash scripts/run_habitat_eval.sh
```

Synthetic delay is a controlled benchmark input, not evidence of validation on
a real Wi-Fi or cellular trace.
