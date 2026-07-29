#!/usr/bin/env python3
# SPDX-License-Identifier: MulanPSL-2.0
"""Habitat episode server for the wiring harness — runs in the Habitat env.

Why a separate process: Habitat lives in a Python 3.9 environment, and neither
rclpy (RoboStack ships py3.10+ only) nor `robonix-api` (`requires-python >=3.10`)
can be installed there. So Habitat stays where it works and speaks a small
length-prefixed msgpack protocol over TCP; `mock_robot`'s HabitatBridgeBody sits
on the other end in the ROS environment and republishes the frames on the camera
and chassis contracts.

This is a TEST HARNESS. It owns a single episode so a RoboNix skill can be driven
against real MP3D-CE imagery, and it makes no metric claims: success rate and SPL
come from InternNav's own evaluator, which owns the benchmark loop and is not
touched by any of this.

Run it from the InternNav workspace (that is where the eval configs live):

    cd internnav-thor-codeonly/workspace/InternNav
    INTERNNAV_HABITAT_DATA_ROOT=<data> PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python \\
        ~/miniconda3/envs/mzh-habitataenv/bin/python <path>/serve.py --port 8799

Protocol (both directions: 4-byte big-endian length, then a msgpack map):
    -> {"op": "info"}   <- {"ok", "intrinsics": {...}, "instruction", "episode_id", "scene"}
    -> {"op": "reset"}  <- {"ok", "rgb", "depth", "position", "yaw", "step_index", "done"}
    -> {"op": "step", "action": "move_forward"}   <- same shape as reset
    -> {"op": "pin_episode", "episode_id": "126"}  <- {"ok", "episode_id"}
    -> {"op": "close"}  <- {"ok"}
"""
from __future__ import annotations

import argparse
import itertools
import logging
import math
import os
import queue
import socket
import socketserver
import struct
import sys
import threading
from concurrent.futures import Future

import msgpack
import numpy as np

log = logging.getLogger("habitat_bridge")

ACTIONS = ("move_forward", "turn_left", "turn_right", "stop")


# ── coordinate conversion ───────────────────────────────────────────────────
def habitat_position_to_ros(position) -> list[float]:
    """Habitat (x right, y up, z backward) -> ROS REP-103 (x forward, y left, z up).

    The agent looks down -z in Habitat, so Habitat's -z is ROS's +x. Getting this
    wrong does not crash anything — it puts the robot somewhere else on the map —
    so it is one function with one job.
    """
    x, y, z = (float(v) for v in np.asarray(position, dtype=np.float64).reshape(3))
    return [-z, -x, y]


def habitat_rotation_to_ros_yaw(rotation) -> float:
    """ROS yaw from the agent's Habitat rotation quaternion.

    Derived from the rotated forward vector rather than read off the quaternion
    directly: component order differs between Habitat's numpy-quaternion (w
    first) and geometry_msgs (w last), and a silent swap is a wrong heading.
    """
    if hasattr(rotation, "w"):
        w, x, y, z = float(rotation.w), float(rotation.x), float(rotation.y), float(rotation.z)
    else:
        x, y, z, w = (float(v) for v in np.asarray(rotation, dtype=np.float64).reshape(4))
    # Rotate Habitat's forward (0, 0, -1) by the quaternion, then project.
    fx = -2.0 * (x * z + w * y)
    fz = -(1.0 - 2.0 * (x * x + y * y))
    return math.atan2(-fx, -fz)


# ── episode ─────────────────────────────────────────────────────────────────
class HabitatEpisode:
    """One Habitat episode, guarded by a lock (habitat.Env is not thread-safe)."""

    def __init__(self, config_path: str, split: str = "", roots: tuple[str, ...] = ()) -> None:
        # Running this as a script puts the script's own directory on sys.path,
        # not the cwd, so the InternNav workspace roots have to be added
        # explicitly. `internnav.habitat_extensions.vln.__init__` also pulls in
        # its evaluator, which imports the top-level `vis_analyze_new` package —
        # that lives beside habitat-lab, not under `internnav`.
        for root in roots:
            resolved = os.path.abspath(root)
            if os.path.isdir(resolved) and resolved not in sys.path:
                sys.path.insert(0, resolved)
                log.info("sys.path += %s", resolved)

        import habitat

        # InternNav registers the VLN task plus its measures (OracleSuccess, ...);
        # without this import habitat.Env raises "invalid oracle_success type".
        import internnav.habitat_extensions.vln.measures  # noqa: F401

        config = habitat.get_config(config_path)
        if split:
            try:
                config.habitat.dataset.split = split
            except Exception:  # noqa: BLE001 - frozen config; keep the file's split
                log.warning("could not override split to %r; using the config's own", split)

        sensor = config.habitat.simulator.agents.main_agent.sim_sensors.depth_sensor
        # Habitat normalises depth to 0..1 by default. The consuming skill decodes
        # 32FC1 as metres, so undo it here rather than shipping a unitless image.
        self._depth_normalized = bool(getattr(sensor, "normalize_depth", False))
        self._depth_min = float(getattr(sensor, "min_depth", 0.0))
        self._depth_max = float(getattr(sensor, "max_depth", 10.0))

        rgb = config.habitat.simulator.agents.main_agent.sim_sensors.rgb_sensor
        width, height, hfov = int(rgb.width), int(rgb.height), float(rgb.hfov)
        fx = (width / 2.0) / math.tan(math.radians(hfov) / 2.0)
        self.intrinsics = {
            "width": width, "height": height,
            "fx": fx, "fy": fx, "cx": width / 2.0, "cy": height / 2.0,
        }

        self._lock = threading.RLock()
        self._env = habitat.Env(config=config)
        self._step_index = 0
        self._instruction = ""
        self._episode_id = ""
        self._scene = ""
        log.info("habitat.Env ready: %dx%d hfov=%.1f depth_normalized=%s max_depth=%.1f",
                 width, height, hfov, self._depth_normalized, self._depth_max)

    def _depth_metres(self, depth) -> np.ndarray:
        array = np.asarray(depth, dtype=np.float32)
        if array.ndim == 3:
            array = array[:, :, 0]
        # Habitat's DepthSensorConfig defaults to normalize_depth=True (unit
        # interval). Some Habitat / InternNav builds still emit metres despite
        # that flag; multiplying those again by max_depth (~10) turns a 2 m
        # couch into a 20 m cliff, and S1 then clips everything to 5 m so the
        # depth channel is a flat wall. Detect the already-metres case by the
        # raw range rather than trusting the flag alone.
        if self._depth_normalized and float(np.nanmax(array)) <= 1.0 + 1e-3:
            array = array * (self._depth_max - self._depth_min) + self._depth_min
        return np.ascontiguousarray(array, dtype=np.float32)

    def _observe(self, obs, done: bool) -> dict:
        state = self._env.sim.get_agent_state()
        rgb = np.ascontiguousarray(np.asarray(obs["rgb"], dtype=np.uint8)[:, :, :3])
        depth = self._depth_metres(obs["depth"])
        return {
            "ok": True,
            "rgb": rgb.tobytes(), "rgb_shape": list(rgb.shape),
            "depth": depth.tobytes(), "depth_shape": list(depth.shape),
            "position": habitat_position_to_ros(state.position),
            "yaw": habitat_rotation_to_ros_yaw(state.rotation),
            "step_index": self._step_index,
            "done": bool(done),
        }

    def info(self) -> dict:
        with self._lock:
            return {
                "ok": True, "intrinsics": dict(self.intrinsics),
                "instruction": self._instruction,
                "episode_id": self._episode_id, "scene": self._scene,
            }

    def pin_episode(self, episode_id: str) -> dict:
        """Make every later reset replay one episode.

        `habitat.Env.reset()` only pulls the next item from the episode iterator,
        so without this the sole way to reach a given episode is to reset past
        every episode before it. Comparing two configurations needs both runs to
        start from the same episode, and re-deriving that start by counting
        resets is fragile. Replacing the iterator with a cycle over the wanted
        episode makes the start reproducible across process restarts too.
        """
        with self._lock:
            wanted = str(episode_id)
            match = next(
                (e for e in self._env.episodes if str(getattr(e, "episode_id", "")) == wanted),
                None,
            )
            if match is None:
                return {"ok": False, "error": f"episode {wanted!r} is not in this split"}
            self._env._episode_iterator = itertools.cycle([match])
            log.info("pinned episode %s; resets now replay it", wanted)
            return {"ok": True, "episode_id": wanted}

    def reset(self) -> dict:
        with self._lock:
            obs = self._env.reset()
            self._step_index = 0
            episode = self._env.current_episode
            self._episode_id = str(getattr(episode, "episode_id", ""))
            self._scene = os.path.basename(str(getattr(episode, "scene_id", "")))
            instruction = getattr(episode, "instruction", None)
            self._instruction = str(getattr(instruction, "instruction_text", "") or "")
            log.info("episode %s (%s): %s", self._episode_id, self._scene, self._instruction[:90])
            return self._observe(obs, done=False)

    def step(self, action: str) -> dict:
        if action not in ACTIONS:
            return {"ok": False, "error": f"unknown action {action!r}; expected one of {ACTIONS}"}
        with self._lock:
            if action == "stop":
                # Do not hand `stop` to the env: it ends the episode and the task
                # then refuses further steps. The skill treats STOP as its own
                # terminal state anyway, so just report the current frame.
                return self._observe(self._env.sim.get_sensor_observations(), done=True)
            obs = self._env.step(action)
            self._step_index += 1
            return self._observe(obs, done=bool(self._env.episode_over))

    def close(self) -> None:
        with self._lock:
            self._env.close()


# ── wire protocol ───────────────────────────────────────────────────────────
def _send(sock: socket.socket, payload: dict) -> None:
    blob = msgpack.packb(payload, use_bin_type=True)
    sock.sendall(struct.pack(">I", len(blob)) + blob)


def _recv_exact(sock: socket.socket, count: int) -> bytes | None:
    chunks = []
    while count:
        chunk = sock.recv(count)
        if not chunk:
            return None
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


def _recv(sock: socket.socket) -> dict | None:
    header = _recv_exact(sock, 4)
    if header is None:
        return None
    body = _recv_exact(sock, struct.unpack(">I", header)[0])
    if body is None:
        return None
    return msgpack.unpackb(body, raw=False)


class _EnvThread:
    """Runs every Habitat call on the thread that created the environment.

    habitat-sim's OpenGL context is thread-affine: rendering from another thread
    fails with "GL::Context::current(): no current context". A ThreadingTCPServer
    hands each client its own thread, so handlers submit work here instead of
    touching the env directly. The env is constructed on this thread too, so the
    context is made current where it will be used.
    """

    def __init__(self, factory) -> None:
        self._factory = factory
        self._queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self.episode: HabitatEpisode | None = None
        self._thread = threading.Thread(target=self._loop, name="habitat-env", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait()
        if self._error is not None:
            raise self._error

    def call(self, fn, timeout: float = 300.0):
        future: Future = Future()
        self._queue.put((fn, future))
        return future.result(timeout=timeout)

    def stop(self) -> None:
        self._queue.put((None, None))
        self._thread.join(timeout=30.0)

    def _loop(self) -> None:
        try:
            self.episode = self._factory()
        except BaseException as exc:  # noqa: BLE001 - surface startup failures to main
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        while True:
            fn, future = self._queue.get()
            if fn is None:
                break
            try:
                future.set_result(fn(self.episode))
            except BaseException as exc:  # noqa: BLE001
                future.set_exception(exc)
        try:
            if self.episode is not None:
                self.episode.close()
        except Exception:  # noqa: BLE001
            log.warning("episode close() raised", exc_info=True)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        env: _EnvThread = self.server.env  # type: ignore[attr-defined]
        log.info("client connected: %s", self.client_address)
        while True:
            try:
                request = _recv(self.request)
            except (ConnectionResetError, OSError):
                break
            if request is None:
                break
            op = str(request.get("op", ""))
            try:
                if op == "info":
                    reply = env.call(lambda e: e.info())
                elif op == "reset":
                    reply = env.call(lambda e: e.reset())
                elif op == "pin_episode":
                    wanted = str(request.get("episode_id", ""))
                    reply = env.call(lambda e, w=wanted: e.pin_episode(w))
                elif op == "step":
                    action = str(request.get("action", ""))
                    reply = env.call(lambda e, a=action: e.step(a))
                elif op == "close":
                    reply = {"ok": True}
                else:
                    reply = {"ok": False, "error": f"unknown op {op!r}"}
            except Exception as exc:  # noqa: BLE001 - one bad request must not kill the server
                log.exception("op %s failed", op)
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            try:
                _send(self.request, reply)
            except (ConnectionResetError, OSError):
                break
            if op == "close":
                break
        log.info("client disconnected: %s", self.client_address)


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    # Handler threads never touch the env; they submit to _EnvThread, which owns
    # both the environment and its GL context.


def main() -> int:
    parser = argparse.ArgumentParser(description="Habitat episode server for the RoboNix wiring harness.")
    parser.add_argument("--config", default="scripts/eval/configs/vln_r2r.yaml",
                        help="Habitat config, relative to the InternNav workspace")
    parser.add_argument("--split", default="", help="override the dataset split; empty keeps the config's")
    parser.add_argument("--root", action="append", default=[],
                        help="directory to prepend to sys.path; repeatable. Pass the InternNav "
                             "workspace root and, if it lives elsewhere, the tree holding "
                             "vis_analyze_new. Defaults to the cwd.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[habitat_bridge] %(levelname)s %(message)s")
    os.environ.setdefault("MAGNUM_LOG", "quiet")
    os.environ.setdefault("HABITAT_SIM_LOG", "quiet")

    roots = tuple(args.root) or (os.getcwd(),)
    env = _EnvThread(lambda: HabitatEpisode(args.config, split=args.split, roots=roots))
    env.start()
    env.call(lambda e: e.reset())  # load the scene now so the first client call is fast

    server = _Server((args.host, args.port), _Handler)
    server.env = env  # type: ignore[attr-defined]
    log.info("serving on %s:%d — Ctrl-C to stop", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        server.server_close()
        env.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
