import io
from unittest.mock import MagicMock, Mock, patch

import httpx
import metrics
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
import tasks
from config import settings


@pytest.fixture(autouse=True)
def mock_healthy_resources():
    with patch.object(metrics, "check_system_resources", return_value=True):
        yield


@pytest.fixture()
def client():
    return TestClient(main.app, follow_redirects=False)


def _png_bytes() -> bytes:
    img = Image.new("RGB", (32, 32), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_queue_ocr_job_returns_accepted_with_status_url(client, monkeypatch):
    captured = {}

    def fake_create_task(task_type, payload):
        captured["task_type"] = task_type
        captured["payload"] = payload
        return "ocr-task-123"

    monkeypatch.setattr(tasks, "create_task", fake_create_task)

    response = client.post(
        "/v1/ai/ocr/jobs",
        files={"image": ("document.png", _png_bytes(), "image/png")},
    )

    assert response.status_code == 202
    data = response.json()
    assert data["success"] is True
    assert data["task_id"] == "ocr-task-123"
    assert data["status"] == "pending"
    assert data["status_url"] == "/v1/ai/jobs/ocr-task-123"
    assert captured["task_type"] == "ocr"
    assert captured["payload"]["image_base64"]
    assert captured["payload"]["content_type"] == "image/png"


def test_queued_ocr_job_rejects_invalid_image(client, monkeypatch):
    create_task = MagicMock()
    monkeypatch.setattr(tasks, "create_task", create_task)

    response = client.post(
        "/v1/ai/ocr/jobs",
        files={"image": ("document.png", b"not-a-real-image", "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"].startswith("{'code': 'invalid_image'")
    create_task.assert_not_called()


def test_task_status_endpoint_returns_local_job_status(client):
    tasks.update_task_status(
        "ocr-task-complete",
        "completed",
        result={"type": "ocr", "result": {"success": True}},
    )

    response = client.get("/v1/ai/jobs/ocr-task-complete")

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "ocr-task-complete"
    assert data["status"] == "completed"
    assert data["result"]["type"] == "ocr"


def test_create_task_propagates_correlation_id_into_async_payload(monkeypatch):
    captured = {}

    def fake_apply_async(args, task_id):
        captured["args"] = args
        captured["task_id"] = task_id

    monkeypatch.setattr(tasks, "ensure_queue_capacity", lambda: None)
    monkeypatch.setattr(
        tasks,
        "get_process_heavy_inference_task",
        lambda: MagicMock(apply_async=fake_apply_async),
    )

    token = main.correlation_id_var.set("trace-ocr-123")
    try:
        task_id = tasks.create_task("ocr", {"image_base64": "payload"})
    finally:
        main.correlation_id_var.reset(token)

    assert task_id == captured["task_id"]
    payload = captured["args"][1]
    assert payload["type"] == "ocr"
    assert payload["correlation_id"] == "trace-ocr-123"
    assert payload["trace_id"] == "trace-ocr-123"


def test_send_webhook_notification_propagates_correlation_headers(monkeypatch):
    recorded = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json=None, headers=None):
            recorded["url"] = url
            recorded["json"] = json
            recorded["headers"] = headers
            return Mock(status_code=200, text="ok")

    monkeypatch.setattr(httpx, "Client", FakeClient)

    tasks.send_webhook_notification(
        "task-456",
        "completed",
        result={"status": "success"},
        correlation_id="trace-callback-123",
    )

    assert recorded["headers"]["X-Correlation-Id"] == "trace-callback-123"
    assert recorded["headers"]["X-Request-Id"] == "trace-callback-123"
    assert recorded["headers"]["trace_id"] == "trace-callback-123"


def test_retry_policy_is_defined_on_heavy_task():
    task = tasks.get_process_heavy_inference_task()

    assert task.max_retries == settings.task_max_retries
    assert task.default_retry_delay == settings.task_retry_delay_seconds
    assert tasks.get_celery_app().conf.task_acks_late is True
    assert tasks.get_celery_app().conf.task_reject_on_worker_lost is True
