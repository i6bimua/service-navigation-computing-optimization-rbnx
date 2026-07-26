# Runtime config accepted by the mock_robot wiring fixture.
#
# Documents the mapping passed as this instance's `config:` value in the local
# verification deployment, delivered to on_init through Driver(CMD_INIT,
# config_json). Documentation only — the provider does not load this file.
#
# An empty `config: {}` uses every default below and needs nothing beyond ROS 2:
# no simulator, no scene assets, no checkpoints, no GPU.
#
# Reminder: the frames this body publishes are heading-dependent gradients with
# no semantic content. It verifies that the skill's wiring works, not that
# navigation works.

config:
  # ── Discrete motion increments ─────────────────────────────────────────
  # These MUST equal the consuming skill's `step_size_m` / `turn_angle_deg`.
  # chassis/move carries the requested magnitude, and a command more than 50%
  # away from these values is rejected rather than executed as a different
  # distance — a silent substitution would desynchronise the policy's belief
  # about where the robot is from where it actually is.
  # float metres, default: 0.25. Must be > 0.
  step_size_m: 0.25
  # float degrees, default: 15.0. Must be > 0.
  turn_angle_deg: 15.0
  # integer, default: 500. Steps after which the body reports episode_over.
  max_steps: 500

  # ── Sensor publication ─────────────────────────────────────────────────
  # float Hz, default: 10.0. Republish rate for the latest frame. The body only
  # produces a new frame when chassis/move advances it, so this governs how
  # often the same frame is re-advertised, not motion speed.
  sensor_hz: 10.0
  # absolute ROS topic names. Change these when two bodies run side by side.
  rgb_topic: /mock_robot/camera/color/image_raw
  depth_topic: /mock_robot/camera/depth/image_raw
  intrinsics_topic: /mock_robot/camera/camera_info
  odom_topic: /mock_robot/chassis/odom
  # string, default: mock_robot_camera. frame_id on the camera and odom
  # messages, and child_frame_id on the odometry.
  frame_id: mock_robot_camera
  # string, default: odom. Parent frame of the published odometry.
  odom_frame_id: odom

  # ── Synthetic sensor geometry ──────────────────────────────────────────
  # integer pixels, default: 224. Must be > 0.
  image_width: 224
  # integer pixels, default: 224. Must be > 0.
  image_height: 224
  # float degrees, default: 79.0. Horizontal FOV; fx/fy are derived from it,
  # matching how simulators configure their sensors.
  hfov_deg: 79.0
