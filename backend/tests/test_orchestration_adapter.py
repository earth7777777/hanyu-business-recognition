from __future__ import annotations

from app.services.orchestration.adapter_registry import get_adapter
from app.services.orchestration.envelope import build_outbound_envelope


class _Rec:
    def __init__(self, rid: str, doc: str):
        self.id = rid
        self.document_type = doc
        self.payload_json = {
            "core": {"customer": "A", "amount": 100},
            "ext": {},
            "attachments": {},
            "trace": {},
        }



def test_copaw_mock_adapter_submit_and_poll():
    adapter = get_adapter("copaw")
    envelope = build_outbound_envelope(
        task_id="t1",
        job_id="j1",
        records=[_Rec("r1", "order")],
    )
    profile = {"provider": "copaw", "mode": "mock"}

    submit = adapter.submit(envelope, profile)
    assert submit["provider"] == "copaw"
    assert submit["status"] == "succeeded"
    assert submit["external_task_id"]

    polled = adapter.poll(submit["external_task_id"], profile, submit_result=submit)
    assert polled["provider"] == "copaw"
    assert polled["status"] == "succeeded"
