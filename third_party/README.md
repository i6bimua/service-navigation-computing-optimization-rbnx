# Third-Party Integration Notes

Do not vendor heavyweight third-party source trees, datasets, model weights, or
experiment logs into RoboNix Compute Optimization. Keep integrations as adapters and document the
external install/download steps.

RoboNix integration is provided through the `robonix_compute.robonix` adapter and HTTP
skill wrapper. Do not copy RoboNix source into this repository; register RoboNix Compute Optimization
as an external skill endpoint.
