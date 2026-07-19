from robonix_compute.common.context_buffer import ContextBuffer


def test_context_buffer_atomic_pending_swap():
    buffer = ContextBuffer()
    buffer.seed("old", step_id=1, request_id="old_req")

    pending = buffer.write_pending("new", step_id=3, request_id="new_req")
    assert pending.latent == "new"
    assert buffer.active().latent == "old"

    active = buffer.swap_pending_to_active()
    assert active is not None
    assert active.latent == "new"
    assert active.latent_step_id == 3
    assert buffer.snapshot(current_step_id=5).misalignment_k == 2


def test_context_buffer_late_absorption_metadata():
    buffer = ContextBuffer()
    buffer.seed("old", step_id=1)

    slot = buffer.absorb(
        "late",
        step_id=2,
        request_id="late_req",
        metadata={"late_absorbed": True},
    )

    assert slot.latent == "late"
    assert slot.metadata["late_absorbed"] is True
    assert buffer.active().request_id == "late_req"
