# Security Policy

## Reporting a Vulnerability

Do not open a public issue for a vulnerability that may expose a robot,
network service, credential, private dataset, or deployment host.

Report the issue to `202330552461@mail.scut.edu.cn` with:

- affected version or commit;
- deployment mode and reachable interface;
- reproduction steps;
- impact assessment;
- suggested mitigation, if available.

## Deployment Boundary

The example WebSocket and HTTP services are research interfaces. Production
deployments must provide authentication, authorization, encryption, network
isolation, request limits, input validation, and safe robot-side action checks.

Retrieved or generated actions must be checked against robot limits, freshness,
collision constraints, and an emergency-stop policy before physical execution.

## Secrets and Data

Never commit tokens, private keys, passwords, robot credentials, private host
paths, licensed datasets, or recorded sensor data containing personal
information. Run `python scripts/release_audit.py` before publishing.
