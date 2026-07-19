import numpy as np

from robonix_compute.common.protocol import ActionRequest, ActionResponse, EpisodeReset, TelemetryEvent
from robonix_compute.common.serialization import packb, unpackb


def test_action_protocol_roundtrip():
    request = ActionRequest(
        request_id="req-action",
        step_id=3,
        observation={"rgb": np.array([1.0, 0.0], dtype=np.float32)},
        instruction="go forward",
    )
    decoded_request = ActionRequest.from_message(unpackb(packb(request.to_message())))

    assert decoded_request.request_id == "req-action"
    assert decoded_request.step_id == 3
    assert decoded_request.instruction == "go forward"
    np.testing.assert_array_equal(decoded_request.observation["rgb"], request.observation["rgb"])

    response = ActionResponse(
        request_id=decoded_request.request_id,
        step_id=decoded_request.step_id,
        action=[1, 2],
        telemetry={"sync_count": 1},
    )
    decoded_response = ActionResponse.from_message(unpackb(packb(response.to_message())))
    assert decoded_response.action == [1, 2]
    assert decoded_response.telemetry["sync_count"] == 1


def test_episode_reset_and_telemetry_event_roundtrip():
    reset = EpisodeReset(episode_id="ep-1", instruction="find the chair")
    decoded_reset = EpisodeReset.from_message(unpackb(packb(reset.to_message())))
    assert decoded_reset.episode_id == "ep-1"
    assert decoded_reset.instruction == "find the chair"

    event = TelemetryEvent(event="sync", payload={"timeout": False})
    decoded_event = TelemetryEvent.from_message(unpackb(packb(event.to_message())))
    assert decoded_event.event == "sync"
    assert decoded_event.payload == {"timeout": False}
