from __future__ import annotations

import json
import sys
import io
from pathlib import Path

import anyio
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fvs_mcp_server import client
from fvs_mcp_server import server


class FakeResponse:
    def __init__(self, payload: dict | bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


def test_normalize_agent_base_accepts_origin_or_agent_prefix():
    assert client.normalize_agent_base("https://app.future.video") == "https://app.future.video/api/agent"
    assert client.normalize_agent_base("https://app.future.video/api/agent") == "https://app.future.video/api/agent"


def test_submit_render_sends_multipart_and_agent_key(monkeypatch, tmp_path):
    upload = tmp_path / "character.png"
    upload.write_bytes(b"\xff\xd8\xfffake-jpeg")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "project_id": "proj_api_test",
                "status": "queued",
                "current_stage": "input",
                "is_running": True,
            }
        )

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    response = client.submit_render(
        request_payload={
            "name": "MCP test",
            "project_mode": "scene",
            "screenplay": "A tiny test scene.",
            "assets": [{"filename": "character.png", "label": "Character"}],
        },
        upload_files=[upload],
        api_key="fvs_test",
        base_url="https://app.future.video",
    )

    assert response["project_id"] == "proj_api_test"
    assert captured["url"] == "https://app.future.video/api/agent/renders"
    assert captured["method"] == "POST"
    headers = {key.lower(): value for key, value in captured["headers"].items()}
    assert headers["x-fvs-agent-key"] == "fvs_test"
    assert headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="request_json"' in captured["data"]
    assert b'filename="character.png"' in captured["data"]
    assert b"Content-Type: image/jpeg" in captured["data"]
    assert b"\xff\xd8\xfffake-jpeg" in captured["data"]


def test_asset_filename_validation_fails_for_missing_upload(tmp_path):
    upload = tmp_path / "other.png"
    upload.write_bytes(b"fake")
    with pytest.raises(client.FVSClientError, match="Missing uploads"):
        client.submit_render(
            request_payload={
                "name": "MCP test",
                "assets": [{"filename": "character.png"}],
            },
            upload_files=[upload],
            api_key="fvs_test",
        )


def test_get_status_uses_project_id_url(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        return FakeResponse({"project_id": "proj_api_test", "status": "completed", "is_running": False})

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    response = client.get_render_status(
        project_id="proj_api_test",
        api_key="fvs_test",
        base_url="https://app.future.video",
    )

    assert response["status"] == "completed"
    assert captured["url"] == "https://app.future.video/api/agent/renders/proj_api_test"
    assert captured["method"] == "GET"


def test_create_paid_render_quote_omits_agent_key_and_returns_402_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["data"] = request.data
        body = json.dumps(
            {
                "quote_id": "quote_test",
                "amount_cents": 120,
                "currency": "usd",
                "payment_url": "https://app.future.video/api/agent/render-quotes/quote_test/pay",
                "status_url": "https://app.future.video/api/agent/paid-renders/quote_test?claim_token=claim",
                "claim_token": "claim",
            }
        ).encode("utf-8")
        raise client.urllib.error.HTTPError(
            request.full_url,
            402,
            "Payment Required",
            {"WWW-Authenticate": 'Payment id="abc", method="stripe"'},
            io.BytesIO(body),
        )

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    response = client.create_paid_render_quote(
        request_payload={"name": "Paid MCP test", "project_mode": "scene", "screenplay": "A tiny test."},
        upload_urls=[{"url": "https://cdn.example.com/ref.jpg", "filename": "ref.jpg"}],
        base_url="https://app.future.video",
    )

    assert response["payment_required"] is True
    assert response["quote_id"] == "quote_test"
    assert response["www_authenticate"].startswith("Payment")
    assert captured["url"] == "https://app.future.video/api/agent/render-quotes"
    headers = {key.lower(): value for key, value in captured["headers"].items()}
    assert "x-fvs-agent-key" not in headers
    payload = json.loads(captured["data"].decode("utf-8"))
    assert payload["request"]["name"] == "Paid MCP test"
    assert payload["upload_urls"][0]["filename"] == "ref.jpg"


def test_get_paid_status_uses_claim_token_url(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        return FakeResponse({"quote_id": "quote_test", "status": "paid"})

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    response = client.get_paid_render_status(
        quote_id="quote_test",
        claim_token="claim token",
        base_url="https://app.future.video",
    )

    assert response["status"] == "paid"
    assert captured["url"] == "https://app.future.video/api/agent/paid-renders/quote_test?claim_token=claim+token"
    headers = {key.lower(): value for key, value in captured["headers"].items()}
    assert "x-fvs-agent-key" not in headers
    assert captured["method"] == "GET"


def test_download_final_video_writes_bytes(monkeypatch, tmp_path):
    def fake_urlopen(request, timeout):
        return FakeResponse(b"video-bytes")

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)
    output = tmp_path / "result.mp4"
    result = client.download_final_video(
        final_video_url="https://signed.example/video.mp4",
        output_path=output,
    )

    assert output.read_bytes() == b"video-bytes"
    assert result["bytes_written"] == len(b"video-bytes")
    assert result["overwritten"] is False


def test_download_final_video_requires_https(tmp_path):
    with pytest.raises(client.FVSClientError, match="absolute HTTPS URL"):
        client.download_final_video(
            final_video_url="http://signed.example/video.mp4",
            output_path=tmp_path / "result.mp4",
        )


def test_download_final_video_refuses_existing_file_without_overwrite(monkeypatch, tmp_path):
    def fake_urlopen(request, timeout):
        return FakeResponse(b"replacement")

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)
    output = tmp_path / "result.mp4"
    output.write_bytes(b"existing")

    with pytest.raises(client.FVSClientError, match="already exists"):
        client.download_final_video(
            final_video_url="https://signed.example/video.mp4",
            output_path=output,
        )

    result = client.download_final_video(
        final_video_url="https://signed.example/video.mp4",
        output_path=output,
        overwrite=True,
    )

    assert output.read_bytes() == b"replacement"
    assert result["overwritten"] is True


def test_agent_api_key_from_context_reads_secret_header():
    class FakeHeaders:
        def get(self, name):
            return {"x-fvs-agent-key": "fvs_live_test"}.get(name)

    class FakeRequest:
        headers = FakeHeaders()

    class FakeRequestContext:
        request = FakeRequest()

    class FakeContext:
        request_context = FakeRequestContext()

    assert server.agent_api_key_from_context(FakeContext()) == "fvs_live_test"


def test_mcp_tool_schema_does_not_expose_timeout_knobs():
    async def _list_tools():
        return await server.mcp.list_tools()

    tools = {
        tool.name: tool.inputSchema
        for tool in anyio.run(_list_tools)
    }

    submit_properties = tools["fvs_submit_render"]["properties"]
    paid_properties = tools["fvs_create_paid_render_quote"]["properties"]
    assert "poll_interval_seconds" not in submit_properties
    assert "poll_timeout_seconds" not in submit_properties
    assert "request_timeout_seconds" not in paid_properties
    assert "request_timeout_seconds" not in tools["fvs_download_final_video"]["properties"]
    assert "overwrite" in tools["fvs_download_final_video"]["properties"]


def test_download_tool_metadata_describes_side_effects():
    async def _list_tools():
        return await server.mcp.list_tools()

    tools = {
        tool.name: tool
        for tool in anyio.run(_list_tools)
    }

    download_tool = tools["fvs_download_final_video"]
    description = download_tool.description or ""
    properties = download_tool.inputSchema["properties"]

    assert "writes the response bytes to output_path" in description
    assert "does not call the FVS Agent API" in description
    assert "600 seconds" in description
    assert download_tool.annotations is not None
    assert download_tool.annotations.readOnlyHint is False
    assert download_tool.annotations.destructiveHint is True
    assert download_tool.annotations.openWorldHint is True
    assert "HTTPS signed final_video_url" in properties["final_video_url"]["description"]
    assert "Local filesystem path" in properties["output_path"]["description"]
    assert "Defaults to false" in properties["overwrite"]["description"]


def test_example_render_request_omits_sync_timeout_field():
    assert "wait_for_completion_seconds" not in server.fvs_example_render_request()


def test_normalize_upload_url_item_allows_explicit_filename():
    url, filename = server.normalize_upload_url_item(
        {"url": "https://cdn.example.com/signed/path", "filename": "lead reference.png"},
        1,
    )

    assert url == "https://cdn.example.com/signed/path"
    assert filename == "lead_reference.png"
