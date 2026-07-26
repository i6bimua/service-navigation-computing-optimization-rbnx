"""Robonix package layer for robonix.skill.compute_optimization.

This subpackage is the Robonix-native boundary: it registers the compute
runtime with Atlas as a Skill provider, exposes the navigate / status /
cancel / telemetry contracts as MCP tools, and drives a real chassis through
`robonix/primitive/chassis/move`.

It sits alongside `robonix_compute.robonix`, the standalone HTTP boundary,
which stays available for orchestrators that are not Robonix deployments.
Both wrap the same `robonix_compute.edge.runtime.EdgeRuntime`.

Nothing here is imported at package-import time: `provider` pulls in
`robonix_api` and the codegen output, which only exist inside a Robonix
deployment. Import `robonix_compute.rbnx.provider` explicitly.
"""

__all__ = ["action_bridge", "controller", "observation", "provider"]
