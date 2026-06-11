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
from fvs_mcp_server import a2a
from fvs_mcp_server import marketplace


EXPECTED_TOOL_ANNOTATIONS = {
    "fvs_open_chatgpt_app": {
        "readOnlyHint": True,
        "openWorldHint": False,
        "destructiveHint": False,
    },
    "fvs_submit_render": {
        "readOnlyHint": False,
        "openWorldHint": True,
        "destructiveHint": True,
    },
    "fvs_create_paid_render_quote": {
        "readOnlyHint": False,
        "openWorldHint": True,
        "destructiveHint": False,
    },
    "fvs_get_render_status": {
        "readOnlyHint": True,
        "openWorldHint": False,
        "destructiveHint": False,
    },
    "fvs_get_paid_render_status": {
        "readOnlyHint": True,
        "openWorldHint": False,
        "destructiveHint": False,
    },
    "fvs_cancel_render": {
        "readOnlyHint": False,
        "openWorldHint": True,
        "destructiveHint": True,
    },
    "fvs_download_final_video": {
        "readOnlyHint": False,
        "openWorldHint": False,
        "destructiveHint": True,
    },
    "fvs_example_render_request": {
        "readOnlyHint": True,
        "openWorldHint": False,
        "destructiveHint": False,
    },
}


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


def test_paid_status_error_redacts_claim_token(monkeypatch):
    def fake_urlopen(request, timeout):
        body = json.dumps({"detail": "Paid render quote not found"}).encode("utf-8")
        raise client.urllib.error.HTTPError(
            request.full_url,
            404,
            "Not Found",
            {},
            io.BytesIO(body),
        )

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(client.FVSClientError) as exc_info:
        client.get_paid_render_status(
            quote_id="quote_review_missing",
            claim_token="claim_review_missing",
            base_url="https://app.future.video",
        )

    error = str(exc_info.value)
    assert "Paid render quote not found" in error
    assert "quote_review_missing" in error
    assert "claim_review_missing" not in error
    assert "claim_token=%5Bredacted%5D" in error


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
    status_properties = tools["fvs_get_render_status"]["properties"]
    paid_status_properties = tools["fvs_get_paid_render_status"]["properties"]
    cancel_properties = tools["fvs_cancel_render"]["properties"]
    assert "poll_interval_seconds" not in submit_properties
    assert "poll_timeout_seconds" not in submit_properties
    assert "api_key" not in submit_properties
    assert "api_key" not in status_properties
    assert "api_key" not in cancel_properties
    assert "base_url" not in submit_properties
    assert "base_url" not in paid_properties
    assert "base_url" not in status_properties
    assert "base_url" not in paid_status_properties
    assert "base_url" not in cancel_properties
    assert "upload_files" not in submit_properties
    assert "request_timeout_seconds" not in paid_properties
    assert "request_timeout_seconds" not in tools["fvs_download_final_video"]["properties"]
    assert "overwrite" in tools["fvs_download_final_video"]["properties"]
    assert submit_properties["request"]["description"].startswith("Required render request object")
    assert paid_properties["request"]["description"].startswith("Required render request object")
    assert "project_mode must be one of scene, music, or custom" in submit_properties["request"]["description"]
    assert "project_mode must be one of scene, music, or custom" in paid_properties["request"]["description"]
    assert "assets[].purpose" in submit_properties["request"]["description"]
    assert "assets[].purpose" in paid_properties["request"]["description"]
    assert "role" not in json.dumps(tools["fvs_submit_render"])
    assert "role" not in json.dumps(tools["fvs_create_paid_render_quote"])
    assert "RenderRequestPayload" in tools["fvs_submit_render"]["$defs"]
    submit_defs = tools["fvs_submit_render"]["$defs"]
    request_properties = submit_defs["RenderRequestPayload"]["properties"]
    asset_properties = submit_defs["RenderAssetPayload"]["properties"]
    assert "screenplay" in request_properties
    assert "name" in submit_defs["RenderRequestPayload"]["required"]
    assert request_properties["project_mode"]["enum"] == ["scene", "music", "custom"]
    assert "role" not in asset_properties
    assert "purpose" in asset_properties
    assert "Use this field for asset intent" in asset_properties["purpose"]["description"]


def test_mcp_server_info_uses_package_version():
    assert server.mcp._mcp_server.version == "0.1.8"


def test_chatgpt_app_tools_link_to_widget_template():
    async def _list_tools():
        return await server.mcp.list_tools()

    tools = {
        tool.name: tool
        for tool in anyio.run(_list_tools)
    }

    expected_tools = set(EXPECTED_TOOL_ANNOTATIONS)
    assert expected_tools.issubset(tools.keys())
    for tool_name in expected_tools:
        annotations = tools[tool_name].annotations
        meta = tools[tool_name].meta or {}
        assert meta["ui"]["resourceUri"] == server.APP_WIDGET_URI
        assert meta["openai/outputTemplate"] == server.APP_WIDGET_URI
        assert meta["openai/widgetAccessible"] is True
        assert annotations is not None
        expected_annotations = EXPECTED_TOOL_ANNOTATIONS[tool_name]
        assert annotations.readOnlyHint is expected_annotations["readOnlyHint"]
        assert annotations.openWorldHint is expected_annotations["openWorldHint"]
        assert annotations.destructiveHint is expected_annotations["destructiveHint"]


def test_submission_tool_annotations_match_mcp_tools():
    async def _list_tools():
        return await server.mcp.list_tools()

    tools = {
        tool.name: tool
        for tool in anyio.run(_list_tools)
    }
    submission = json.loads((ROOT / "chatgpt-app-submission.json").read_text(encoding="utf-8"))

    assert set(EXPECTED_TOOL_ANNOTATIONS).issubset(submission["tools"].keys())
    for tool_name, expected_annotations in EXPECTED_TOOL_ANNOTATIONS.items():
        tool_annotations = tools[tool_name].annotations
        submission_annotations = submission["tools"][tool_name]["annotations"]
        assert tool_annotations is not None
        assert submission_annotations == expected_annotations
        for hint_name, expected_value in expected_annotations.items():
            assert submission_annotations[hint_name] is expected_value
            assert getattr(tool_annotations, hint_name) is expected_value


def test_chatgpt_app_widget_resource_has_apps_mime_and_csp():
    async def _read_widget():
        return await server.mcp.read_resource(server.APP_WIDGET_URI)

    contents = anyio.run(_read_widget)

    assert len(contents) == 1
    widget = contents[0]
    assert widget.mime_type == "text/html;profile=mcp-app"
    assert "Future Video Studio" in widget.content
    assert "fvs_create_paid_render_quote" in widget.content
    assert widget.meta["ui"]["domain"] == "https://mcp.future.video"
    assert "https://app.future.video" in widget.meta["ui"]["csp"]["connectDomains"]
    assert not any("*" in domain for domain in widget.meta["ui"]["csp"]["resourceDomains"])


def test_submission_test_cases_are_review_runnable():
    submission = json.loads((ROOT / "chatgpt-app-submission.json").read_text(encoding="utf-8"))
    positive_cases = submission["test_cases"]
    negative_cases = submission["negative_test_cases"]

    assert len(positive_cases) >= 5
    assert len(negative_cases) >= 3

    submitted_text = "\n".join(
        f"{case.get('user_prompt', '')}\n{case.get('expected_output', '')}"
        for case in positive_cases
    )
    for stale_phrase in (
        "proj_example_123",
        "my quote ID and claim token",
        "this completed render URL",
        "using my connected FVS account",
    ):
        assert stale_phrase not in submitted_text

    positive_tools = "\n".join(str(case.get("tools_triggered") or "") for case in positive_cases)
    assert "fvs_open_chatgpt_app" in positive_tools
    assert "fvs_example_render_request" in positive_tools
    assert "fvs_create_paid_render_quote" in positive_tools
    assert "fvs_submit_render" not in positive_tools
    assert "fvs_cancel_render" not in positive_tools
    assert "fvs_download_final_video" not in positive_tools
    assert "fvs_get_render_status" not in positive_tools
    assert "redacts the claim token value" in submitted_text


def test_openai_apps_challenge_route_returns_stable_plaintext(monkeypatch):
    monkeypatch.delenv("OPENAI_APPS_CHALLENGE_RESPONSE", raising=False)
    response = anyio.run(server.openai_apps_challenge, None)

    assert response.status_code == 200
    assert response.media_type.startswith("text/plain")
    assert response.body == b"openai-apps-challenge: future-video-studio\n"


def test_openai_apps_challenge_route_allows_exact_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI_APPS_CHALLENGE_RESPONSE", "custom-openai-token")
    response = anyio.run(server.openai_apps_challenge, None)

    assert response.status_code == 200
    assert response.body == b"custom-openai-token\n"


def test_open_chatgpt_app_returns_side_effect_free_snapshot():
    snapshot = server.fvs_open_chatgpt_app(project_id="proj_test", final_video_url="https://signed.example/final.mp4")

    assert snapshot["app"] == "future-video-studio"
    assert snapshot["status"] == "ready"
    assert snapshot["project_id"] == "proj_test"
    assert snapshot["final_video_url"] == "https://signed.example/final.mp4"
    assert snapshot["default_request"]["video_resolution"] == "720p"


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
    assert download_tool.annotations.openWorldHint is False
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


def test_a2a_agent_card_exposes_marketplace_and_mcp_metadata():
    card = a2a.build_agent_card(base_url="https://mcp.future.video")

    assert card["protocolVersion"] == "1.0"
    assert card["url"] == "https://mcp.future.video"
    assert card["metadata"]["mcpRegistryName"] == "video.future/future-video-studio"
    assert card["metadata"]["mcpEndpoint"] == "https://mcp.future.video/mcp"
    assert card["securitySchemes"]["fvsAgentKey"]["name"] == "X-FVS-Agent-Key"
    assert card["securitySchemes"]["fvsMarketplaceAccount"]["name"] == "X-FVS-Marketplace-Account"
    assert "pay-per-render" in card["metadata"]["billingModes"]
    assert any(skill["id"] == "create-video-render" for skill in card["skills"])


def test_a2a_extract_text_message_creates_default_render_request():
    submission = a2a.extract_a2a_render_submission(
        {
            "message": {
                "messageId": "msg_1",
                "contextId": "ctx_1",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "text": "Create a 20 second product launch video for a startup selling AI editing software."
                    }
                ],
                "metadata": {
                    "shot_count": 4,
                    "scene_target_duration_seconds": 20,
                    "video_resolution": "720p",
                },
            }
        }
    )

    assert submission["context_id"] == "ctx_1"
    assert submission["message_id"] == "msg_1"
    assert submission["upload_urls"] == []
    assert submission["request"]["project_mode"] == "scene"
    assert submission["request"]["shot_count"] == 4
    assert submission["request"]["scene_target_duration_seconds"] == 20
    assert "startup selling AI editing software" in submission["request"]["screenplay"]


def test_a2a_extract_structured_request_and_file_parts():
    submission = a2a.extract_a2a_render_submission(
        {
            "message": {
                "parts": [
                    {
                        "data": {
                            "fvs_render_request": {
                                "name": "Enterprise explainer",
                                "project_mode": "scene",
                                "screenplay": "Shot 1: A team reviews dashboards.",
                                "shot_count": 1,
                            },
                            "upload_urls": [
                                {"url": "https://assets.example.com/logo.png", "filename": "logo.png"}
                            ],
                        }
                    },
                    {
                        "file": {
                            "uri": "https://assets.example.com/reference.jpg",
                            "name": "reference.jpg",
                        }
                    },
                ]
            }
        }
    )

    assert submission["request"]["name"] == "Enterprise explainer"
    assert submission["request"]["shot_count"] == 1
    assert submission["upload_urls"] == [
        {"url": "https://assets.example.com/reference.jpg", "filename": "reference.jpg"},
        {"url": "https://assets.example.com/logo.png", "filename": "logo.png"},
    ]


def test_a2a_build_task_response_attaches_final_video_artifact():
    task = a2a.build_task_from_render_response(
        {
            "project_id": "proj_api_test",
            "status": "completed",
            "current_stage": "completed",
            "is_running": False,
            "final_video_url": "https://signed.example.com/final.mp4",
            "status_url": "https://app.future.video/api/agent/renders/proj_api_test",
            "asset_count": 1,
            "clip_count": 3,
        },
        context_id="ctx_1",
    )

    assert task["id"] == "proj_api_test"
    assert task["contextId"] == "ctx_1"
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["file"]["uri"] == "https://signed.example.com/final.mp4"
    assert task["metadata"]["futureVideoStudio"]["clipCount"] == 3


def test_a2a_agent_api_key_from_headers_accepts_bearer():
    class FakeHeaders:
        def get(self, name):
            return {"authorization": "Bearer fvs_live_bearer"}.get(name)

    assert a2a.agent_api_key_from_headers(FakeHeaders()) == "fvs_live_bearer"


def test_a2a_billing_mode_from_header_and_metadata():
    class FakeHeaders:
        def get(self, name):
            return {"x-fvs-billing-mode": "paid_quote"}.get(name)

    assert a2a.billing_mode_from_request({"message": {"parts": []}}, FakeHeaders()) == "pay-per-render"
    assert (
        a2a.billing_mode_from_request(
            {
                "message": {
                    "parts": [],
                    "metadata": {"billingMode": "marketplace"},
                }
            },
            {},
        )
        == "marketplace-linked-account"
    )


def test_a2a_paid_quote_task_keeps_payment_details_in_metadata():
    task = a2a.build_task_from_paid_quote_response(
        {
            "payment_required": True,
            "quote_id": "quote_test",
            "amount_cents": 125,
            "currency": "usd",
            "credits_quoted": 125,
            "payment_url": "https://app.future.video/api/agent/render-quotes/quote_test/pay?claim_token=claim",
            "status_url": "https://app.future.video/api/agent/paid-renders/quote_test?claim_token=claim",
            "claim_token": "claim",
            "www_authenticate": 'Payment id="quote_test", method="stripe"',
        },
        context_id="ctx_paid",
    )

    assert task["id"] == "quote_test"
    assert task["contextId"] == "ctx_paid"
    assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    payment = task["metadata"]["futureVideoStudio"]
    assert payment["paymentRequired"] is True
    assert payment["claimToken"] == "claim"
    assert payment["amountCents"] == 125
    assert task["status"]["message"]["parts"][1]["data"]["futureVideoStudioPayment"]["payment_url"].startswith(
        "https://app.future.video"
    )


def test_marketplace_credential_resolves_mapped_signed_account(monkeypatch):
    timestamp = "1800000000"
    secret = "marketplace-secret"
    signature = marketplace.marketplace_signature(
        secret=secret,
        timestamp=timestamp,
        account_id="acct_google_123",
        entitlement_id="ent_123",
    )
    monkeypatch.setenv("FVS_MARKETPLACE_SHARED_SECRET", secret)
    monkeypatch.setenv("FVS_MARKETPLACE_REQUIRE_SIGNATURE", "true")
    monkeypatch.setenv(
        "FVS_MARKETPLACE_ACCOUNT_KEYS_JSON",
        json.dumps(
            {
                "acct_google_123": {
                    "api_key": "fvs_live_marketplace",
                    "entitlement_id": "ent_123",
                    "plan": "starter",
                    "status": "active",
                }
            }
        ),
    )

    class FakeHeaders:
        def get(self, name):
            return {
                "x-fvs-marketplace-account": "acct_google_123",
                "x-fvs-marketplace-entitlement": "ent_123",
                "x-fvs-marketplace-timestamp": timestamp,
                "x-fvs-marketplace-signature": f"sha256={signature}",
            }.get(name)

    credential = marketplace.resolve_marketplace_credential(FakeHeaders(), now=float(timestamp))

    assert credential is not None
    assert credential.api_key == "fvs_live_marketplace"
    assert credential.account_id == "acct_google_123"
    assert credential.entitlement_id == "ent_123"


def test_marketplace_signature_rejects_tampering(monkeypatch):
    timestamp = "1800000000"
    monkeypatch.setenv("FVS_MARKETPLACE_SHARED_SECRET", "marketplace-secret")
    monkeypatch.setenv("FVS_MARKETPLACE_REQUIRE_SIGNATURE", "true")
    monkeypatch.setenv(
        "FVS_MARKETPLACE_ACCOUNT_KEYS_JSON",
        json.dumps({"acct_google_123": {"api_key": "fvs_live_marketplace", "status": "active"}}),
    )

    class FakeHeaders:
        def get(self, name):
            return {
                "x-fvs-marketplace-account": "acct_google_123",
                "x-fvs-marketplace-timestamp": timestamp,
                "x-fvs-marketplace-signature": "sha256=bad",
            }.get(name)

    with pytest.raises(marketplace.MarketplaceAuthError, match="signature is invalid"):
        marketplace.resolve_marketplace_credential(FakeHeaders(), now=float(timestamp))


def test_server_context_resolves_marketplace_agent_key(monkeypatch):
    monkeypatch.setenv(
        "FVS_MARKETPLACE_ACCOUNT_KEYS_JSON",
        json.dumps({"acct_google_123": {"api_key": "fvs_live_marketplace", "status": "active"}}),
    )

    class FakeHeaders:
        def get(self, name):
            return {"x-fvs-marketplace-account": "acct_google_123"}.get(name)

    class FakeRequest:
        headers = FakeHeaders()

    class FakeRequestContext:
        request = FakeRequest()

    class FakeContext:
        request_context = FakeRequestContext()

    assert server.resolve_agent_api_key(api_key=None, ctx=FakeContext()) == "fvs_live_marketplace"
