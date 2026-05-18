from __future__ import annotations

import io
import time
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _set_integration_hub() -> None:
    headers = {"X-Role": "admin"}
    body = {
        "value": {
            "default_provider": "copaw",
            "providers": {
                "copaw": {
                    "provider_type": "copaw",
                    "enabled": True,
                    "ingest_mode": "pull",
                    "result_mode": "pull",
                    "mapping": {},
                    "transport": {
                        "mode": "mock",
                        "submit_url": "",
                        "result_url": "",
                        "callback_url": "",
                        "timeout_seconds": 30,
                    },
                    "auth": {},
                    "signature": {"enabled": False, "algorithm": "hmac_sha256", "secret": ""},
                }
            },
            "auth_clients": {
                "it-client": {
                    "enabled": True,
                    "token": "it-token",
                    "providers": ["copaw"],
                    "allow_doc_types": ["order", "shipment", "payment_notice", "invoice"],
                }
            },
            "policies": {
                "idempotency_key": "request_id",
                "allow_push_callback": False,
                "max_files_per_job": 20,
            },
        }
    }
    resp = client.put("/v1/config/integration_hub", headers=headers, json=body)
    assert resp.status_code == 200


def test_external_intake_and_results_pull():
    _set_integration_hub()
    req_id = f"req-open-{int(time.time() * 1000)}"

    ext_headers = {
        "X-Client-Id": "it-client",
        "X-Client-Token": "it-token",
    }

    create_resp = client.post(
        "/v1/intake/jobs",
        headers=ext_headers,
        json={
            "provider": "copaw",
            "request_id": req_id,
            "source_ref": "copaw-case-1",
            "metadata": {"from": "test"},
        },
    )
    assert create_resp.status_code == 200
    body = create_resp.json()
    assert body["provider"] == "copaw"
    job_id = body["job"]["id"]

    due = (date.today() + timedelta(days=2)).isoformat()
    csv_content = (
        "客户,合同号,客户订单号,料号,品名,数量,金额,订单日期,交期\n"
        f"A客户,HT-100,SO-100,ITEM-100,产品100,100,1000,2026-03-18,{due}\n"
    )
    upload_resp = client.post(
        f"/v1/intake/jobs/{job_id}/files",
        headers=ext_headers,
        data={"document_type": "order", "provider": "copaw"},
        files={"upload": ("order.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json()["parsed_count"] >= 1

    run_resp = client.post(
        f"/v1/intake/jobs/{job_id}/run",
        headers=ext_headers,
        json={"provider": "copaw", "request_id": req_id, "source_ref": "copaw-case-1"},
    )
    assert run_resp.status_code == 200
    task_id = run_resp.json()["id"]

    status = "queued"
    for _ in range(20):
        info = client.get(
            f"/v1/intake/jobs/{job_id}/status",
            headers=ext_headers,
            params={"provider": "copaw"},
        )
        assert info.status_code == 200
        tasks = info.json()["tasks"]
        if tasks:
            status = tasks[0]["status"]
        if status in {"succeeded", "failed"}:
            break
        time.sleep(0.1)
    assert status == "succeeded"

    result_resp = client.get(
        f"/v1/results/jobs/{job_id}",
        headers=ext_headers,
        params={"provider": "copaw", "task_id": task_id},
    )
    assert result_resp.status_code == 200
    result = result_resp.json()
    assert result["provider"] == "copaw"
    assert result["result"]["job_id"] == job_id
    assert "alerts_summary" in result["result"]
    assert "provider_view" in result

    export_resp = client.get(
        f"/v1/results/jobs/{job_id}/export",
        headers=ext_headers,
        params={"provider": "copaw", "kind": "alerts"},
    )
    assert export_resp.status_code == 200
    assert "text/csv" in export_resp.headers.get("content-type", "")
