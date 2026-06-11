from __future__ import annotations

import asyncio
import json
import os
import ipaddress
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

try:
    from .client import (
        DEFAULT_BASE_URL,
        DEFAULT_POLL_INTERVAL_SECONDS,
        DEFAULT_POLL_TIMEOUT_SECONDS,
        FVSClientError,
        cancel_render,
        create_paid_render_quote,
        download_final_video,
        get_paid_render_status,
        get_render_status,
        submit_render,
    )
    from .a2a import (
        A2ARequestError,
        BILLING_MODE_ACCOUNT,
        BILLING_MODE_MARKETPLACE,
        BILLING_MODE_PAY_PER_RENDER,
        a2a_error_body,
        agent_api_key_from_headers,
        billing_mode_from_request,
        build_agent_card,
        build_task_from_paid_quote_response,
        build_task_from_render_response,
        extract_a2a_render_submission,
    )
    from .marketplace import (
        MarketplaceAuthError,
        MarketplaceCredential,
        credential_metadata,
        resolve_marketplace_credential,
    )
    from .chatgpt_app import (
        APP_RESOURCE_META,
        APP_RESOURCE_MIME_TYPE,
        APP_WIDGET_URI,
        CHATGPT_WIDGET_HTML,
        app_tool_meta,
        chatgpt_app_manifest,
        open_app_snapshot,
    )
except ImportError:  # Allows direct `python path\to\server.py` execution.
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fvs_mcp_server.client import (  # type: ignore[no-redef]
        DEFAULT_BASE_URL,
        DEFAULT_POLL_INTERVAL_SECONDS,
        DEFAULT_POLL_TIMEOUT_SECONDS,
        FVSClientError,
        cancel_render,
        create_paid_render_quote,
        download_final_video,
        get_paid_render_status,
        get_render_status,
        submit_render,
    )
    from fvs_mcp_server.a2a import (  # type: ignore[no-redef]
        A2ARequestError,
        BILLING_MODE_ACCOUNT,
        BILLING_MODE_MARKETPLACE,
        BILLING_MODE_PAY_PER_RENDER,
        a2a_error_body,
        agent_api_key_from_headers,
        billing_mode_from_request,
        build_agent_card,
        build_task_from_paid_quote_response,
        build_task_from_render_response,
        extract_a2a_render_submission,
    )
    from fvs_mcp_server.marketplace import (  # type: ignore[no-redef]
        MarketplaceAuthError,
        MarketplaceCredential,
        credential_metadata,
        resolve_marketplace_credential,
    )
    from fvs_mcp_server.chatgpt_app import (  # type: ignore[no-redef]
        APP_RESOURCE_META,
        APP_RESOURCE_MIME_TYPE,
        APP_WIDGET_URI,
        CHATGPT_WIDGET_HTML,
        app_tool_meta,
        chatgpt_app_manifest,
        open_app_snapshot,
    )


INSTRUCTIONS = """
Create videos through the Future Video Studio Agent API.

In ChatGPT, this MCP server also exposes a Future Video Studio app widget for
submitting, paying for, polling, and opening renders directly inside the
conversation.

Use fvs_submit_render for account API-key projects, fvs_create_paid_render_quote
for no-account Link payment quotes, fvs_get_render_status or
fvs_get_paid_render_status to poll, fvs_cancel_render to stop account-owned runs,
and fvs_download_final_video to save a finished signed final_video_url. Prefer
FVS_AGENT_API_KEY and FVS_AGENT_BASE_URL from the MCP server environment when
the user has an FVS account. Agent API keys are owned by normal FVS user
accounts and use the same pricing, wallet, and saved pipeline defaults as the
web app. Marketplace-linked calls map a signed marketplace account to an FVS
Agent API key. Paid quotes use the same credit estimate and return a claim token
so the result can be retrieved without an FVS account.
""".strip()

API_REFERENCE = """
# Future Video Studio Agent API

Default app origin: https://app.future.video
Route prefix: /api/agent

Authentication:
- X-FVS-Agent-Key: <agent key> for account wallet mode
- X-FVS-Marketplace-Account plus signed marketplace headers for linked Marketplace mode
- no key or X-FVS-Billing-Mode: pay-per-render for paid quote mode through Link/Stripe MPP

Endpoints:
- POST /renders
- GET /renders/{project_id}
- POST /renders/{project_id}/cancel
- POST /render-quotes
- GET or POST /render-quotes/{quote_id}/pay
- GET /paid-renders/{quote_id}?claim_token=...

Render payload highlights:
- name is required
- project_mode: music, scene, or custom
- screenplay and instructions describe the video
- shot_count range: 1 to 64
- scene_target_duration_seconds range: 4 to 600
- optional model fields inherit the owning user's saved settings when omitted
- assets[].filename must match an uploaded file basename

Billing:
- Agent renders use the same FVS credit model as the app.
- The owning user account's wallet is reserved and settled by the backend.
- Marketplace-linked renders map a signed marketplace account to an FVS account key.
- Paid quote mode returns HTTP 402 with `WWW-Authenticate: Payment ... method="stripe"`.
- Agents can pay the quote with Link, then poll the paid status URL using the claim token.
""".strip()

FINAL_VIDEO_DOWNLOAD_DESCRIPTION = """
Download a completed Future Video Studio final render URL to a local file.

Use this only after fvs_get_render_status or fvs_get_paid_render_status returns
a final_video_url for a completed render. The tool performs an unauthenticated
HTTPS GET to that signed URL and writes the response bytes to output_path on the
MCP server's local filesystem. It does not call the FVS Agent API, spend wallet
credits, require FVS_AGENT_API_KEY, cancel jobs, or modify remote render state.

Side effects and constraints: output_path is a local filesystem path for the MCP
server process, parent directories are created, existing files are not replaced
unless overwrite is true, and large videos may take minutes to download. The
request timeout is 600 seconds. Use a fresh status check to refresh expired
signed URLs, and do not pass arbitrary or untrusted URLs.
""".strip()

SERVER_MANIFEST = {
    "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
    "name": "video.future/future-video-studio",
    "title": "Future Video Studio",
    "description": "Create and manage cinematic AI video renders through the Future Video Studio Agent API.",
    "repository": {
        "url": "https://github.com/ariadne-coil/fvs-mcp",
        "source": "github",
        "id": "ariadne-coil/fvs-mcp",
    },
    "websiteUrl": "https://future.video",
    "icons": [
        {
            "src": "https://future.video/visuals/FutureVideoIcon.png",
            "mimeType": "image/png",
        }
    ],
    "version": "0.1.6",
    "chatgptApp": {
        "connectorUrl": "https://mcp.future.video/mcp",
        "widgetResource": APP_WIDGET_URI,
        "manifestUrl": "https://mcp.future.video/chatgpt-app.json",
        "description": "ChatGPT app widget for creating and monitoring Future Video Studio renders.",
    },
    "remotes": [
        {
            "type": "streamable-http",
            "url": "https://mcp.future.video/mcp",
            "headers": [
                {
                    "name": "X-FVS-Agent-Key",
                    "description": "Optional Future Video Studio Agent API key from Settings > Agent API Access. Omit for Link paid quote mode.",
                    "isRequired": False,
                    "isSecret": True,
                },
                {
                    "name": "X-FVS-Billing-Mode",
                    "description": "Optional billing mode: account-wallet, marketplace-linked-account, or pay-per-render.",
                    "isRequired": False,
                    "isSecret": False,
                },
                {
                    "name": "X-FVS-Marketplace-Account",
                    "description": "Optional linked marketplace account identifier for enterprise procurement flows.",
                    "isRequired": False,
                    "isSecret": False,
                },
                {
                    "name": "X-FVS-Marketplace-Entitlement",
                    "description": "Optional marketplace entitlement identifier paired with X-FVS-Marketplace-Account.",
                    "isRequired": False,
                    "isSecret": False,
                },
                {
                    "name": "X-FVS-Marketplace-Timestamp",
                    "description": "Optional Unix timestamp used in marketplace signature verification.",
                    "isRequired": False,
                    "isSecret": False,
                },
                {
                    "name": "X-FVS-Marketplace-Signature",
                    "description": "Optional HMAC-SHA256 signature from the marketplace account-linking gateway.",
                    "isRequired": False,
                    "isSecret": True,
                }
            ],
        }
    ],
    "packages": [
        {
            "registryType": "pypi",
            "identifier": "future-video-studio-mcp",
            "version": "0.1.6",
            "transport": {
                "type": "stdio",
            },
        }
    ],
}

DEFAULT_ALLOWED_HOSTS = [
    "mcp.future.video",
    "fmv-studio-mcp-t7cat7yhuq-uc.a.run.app",
    "fmv-studio-mcp-258034109040.us-central1.run.app",
    "localhost:*",
    "127.0.0.1:*",
]
DEFAULT_ALLOWED_ORIGINS = [
    "https://mcp.future.video",
    "https://app.future.video",
    "https://future.video",
]
MAX_REMOTE_UPLOAD_BYTES = 100 * 1024 * 1024


class RenderAssetPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    filename: str | None = Field(
        default=None,
        description=(
            "Basename of an uploaded or referenced asset. When upload_urls is used, "
            "this must match an upload_urls filename or URL basename."
        ),
    )
    label: str | None = Field(
        default=None,
        description="Short label for the asset, such as Character, Logo, or Reference.",
    )
    purpose: str | None = Field(
        default=None,
        description=(
            "Optional guidance for how this asset should be used, such as "
            "character_reference, location_reference, logo_reference, style_reference, "
            "music_reference, or document_reference."
        ),
    )


class RenderRequestPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(description="Required project name for the Future Video Studio render.")
    project_mode: Literal["scene", "music", "custom"] = Field(
        default="scene",
        description="Render mode: scene for cinematic scenes, music for music-first videos, or custom for advanced requests.",
    )
    screenplay: str | None = Field(
        default=None,
        description="Shot-by-shot creative brief or script describing what should happen in the video.",
    )
    instructions: str | None = Field(
        default=None,
        description="Additional direction for continuity, style, camera movement, subtitles, audio, or constraints.",
    )
    shot_count: int | None = Field(
        default=None,
        ge=1,
        le=64,
        description="Target number of shots or clips to plan for the render.",
    )
    scene_target_duration_seconds: float | None = Field(
        default=None,
        ge=4,
        le=600,
        description="Target total scene duration in seconds.",
    )
    video_resolution: str | None = Field(
        default=None,
        description="Requested output resolution, such as 720p or 1080p.",
    )
    visual_style_preset: str | None = Field(
        default=None,
        description="Optional FVS visual style preset, such as realistic_cinematic.",
    )
    assets: list[RenderAssetPayload] | None = Field(
        default=None,
        description="Optional reference assets that correspond to uploaded or public upload_urls.",
    )


class UploadUrlPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str = Field(description="Public HTTPS URL for a reference asset.")
    filename: str | None = Field(
        default=None,
        description="Optional basename used to match request.assets[].filename.",
    )


def csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return [part.strip() for part in raw.split(",") if part.strip()]


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


mcp = FastMCP(
    "future-video-studio",
    instructions=INSTRUCTIONS,
    host=os.getenv("FVS_MCP_HOST", "0.0.0.0"),
    port=int_env("PORT", int_env("FVS_MCP_PORT", 8000)),
    streamable_http_path=os.getenv("FVS_MCP_PATH", "/mcp"),
    json_response=True,
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=bool_env("FVS_MCP_DNS_REBINDING_PROTECTION", True),
        allowed_hosts=csv_env("FVS_MCP_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS),
        allowed_origins=csv_env("FVS_MCP_ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS),
    ),
)


@mcp.resource("fvs://agent-api/reference")
def agent_api_reference() -> str:
    """Return the compact Future Video Studio Agent API reference."""
    return API_REFERENCE


@mcp.resource(
    APP_WIDGET_URI,
    name="future_video_studio_chatgpt_app",
    title="Future Video Studio",
    description="ChatGPT app widget for creating and monitoring Future Video Studio renders.",
    mime_type=APP_RESOURCE_MIME_TYPE,
    meta=APP_RESOURCE_META,
)
def chatgpt_app_widget() -> str:
    """Return the Future Video Studio ChatGPT app widget template."""
    return CHATGPT_WIDGET_HTML


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health_check(request: Request) -> Response:
    return JSONResponse({"status": "ok", "service": "future-video-studio-mcp"})


@mcp.custom_route("/", methods=["GET"], include_in_schema=False)
async def root(request: Request) -> Response:
    return JSONResponse(
        {
            "service": "future-video-studio-mcp",
            "mcp_endpoint": "/mcp",
            "manifest": "/server.json",
            "chatgpt_app_manifest": "/chatgpt-app.json",
            "chatgpt_connector_url": "/mcp",
            "a2a_agent_card": "/.well-known/agent-card.json",
            "a2a_message_endpoint": "/message:send",
        }
    )


@mcp.custom_route("/server.json", methods=["GET"], include_in_schema=False)
async def server_manifest(request: Request) -> Response:
    return JSONResponse(SERVER_MANIFEST)


@mcp.custom_route("/.well-known/mcp-server.json", methods=["GET"], include_in_schema=False)
async def well_known_server_manifest(request: Request) -> Response:
    return JSONResponse(SERVER_MANIFEST)


@mcp.custom_route("/chatgpt-app.json", methods=["GET"], include_in_schema=False)
async def chatgpt_app_manifest_route(request: Request) -> Response:
    return JSONResponse(chatgpt_app_manifest())


@mcp.custom_route("/.well-known/fvs-chatgpt-app.json", methods=["GET"], include_in_schema=False)
async def well_known_chatgpt_app_manifest_route(request: Request) -> Response:
    return JSONResponse(chatgpt_app_manifest())


@mcp.custom_route("/.well-known/openai-apps-challenge", methods=["GET"], include_in_schema=False)
async def openai_apps_challenge(request: Request) -> Response:
    body = os.getenv("OPENAI_APPS_CHALLENGE_RESPONSE", "").strip()
    if not body:
        body = "openai-apps-challenge: future-video-studio\n"
    if not body.endswith("\n"):
        body = f"{body}\n"
    return Response(body, media_type="text/plain; charset=utf-8")


@mcp.custom_route("/.well-known/agent-card.json", methods=["GET"], include_in_schema=False)
async def a2a_well_known_agent_card(request: Request) -> Response:
    return JSONResponse(build_agent_card(base_url=_a2a_request_base_url(request)))


@mcp.custom_route("/.well-known/agent.json", methods=["GET"], include_in_schema=False)
async def a2a_legacy_well_known_agent_card(request: Request) -> Response:
    return JSONResponse(build_agent_card(base_url=_a2a_request_base_url(request)))


@mcp.custom_route("/agent-card.json", methods=["GET"], include_in_schema=False)
async def a2a_agent_card(request: Request) -> Response:
    return JSONResponse(build_agent_card(base_url=_a2a_request_base_url(request)))


@mcp.custom_route("/extendedAgentCard", methods=["GET"], include_in_schema=False)
async def a2a_extended_agent_card(request: Request) -> Response:
    return JSONResponse(build_agent_card(base_url=_a2a_request_base_url(request)))


@mcp.custom_route("/message:send", methods=["POST"], include_in_schema=False)
async def a2a_send_message(request: Request) -> Response:
    try:
        body = await _a2a_json_body(request)
        billing_mode = billing_mode_from_request(body, request.headers)
        submission = extract_a2a_render_submission(body)

        api_key = agent_api_key_from_headers(request.headers)
        marketplace_credential: MarketplaceCredential | None = None
        if billing_mode == BILLING_MODE_MARKETPLACE:
            marketplace_credential = resolve_marketplace_credential(request.headers)
            if marketplace_credential is not None:
                api_key = marketplace_credential.api_key
        elif billing_mode is None and not api_key:
            marketplace_credential = resolve_marketplace_credential(request.headers)
            if marketplace_credential is not None:
                api_key = marketplace_credential.api_key
                billing_mode = BILLING_MODE_MARKETPLACE

        if billing_mode == BILLING_MODE_ACCOUNT and not api_key:
            return _a2a_error_response(
                code="unauthenticated",
                message="Account-wallet A2A renders require X-FVS-Agent-Key or Authorization: Bearer <agent key>.",
                status_code=401,
            )

        if billing_mode == BILLING_MODE_MARKETPLACE and marketplace_credential is None:
            return _a2a_error_response(
                code="marketplace_unauthenticated",
                message="Marketplace-linked A2A renders require a linked marketplace account header set.",
                status_code=401,
            )

        if billing_mode == BILLING_MODE_PAY_PER_RENDER or not api_key:
            result = await asyncio.to_thread(
                fvs_create_paid_render_quote,
                submission["request"],
                submission["upload_urls"],
                None,
            )
            task = build_task_from_paid_quote_response(result, context_id=submission.get("context_id"))
            _attach_a2a_billing_metadata(task, BILLING_MODE_PAY_PER_RENDER, marketplace_credential=None)
            status_code = 402 if result.get("payment_required") else 200
            return JSONResponse({"task": task}, status_code=status_code, headers=_a2a_paid_headers(result))

        result = await asyncio.to_thread(
            submit_account_render_with_key,
            request=submission["request"],
            upload_urls=submission["upload_urls"],
            api_key=api_key,
        )
        task = build_task_from_render_response(result, context_id=submission.get("context_id"))
        _attach_a2a_billing_metadata(
            task,
            billing_mode or (BILLING_MODE_MARKETPLACE if marketplace_credential else BILLING_MODE_ACCOUNT),
            marketplace_credential=marketplace_credential,
        )
        return JSONResponse({"task": task})
    except A2ARequestError as exc:
        return _a2a_error_response(code=exc.code, message=str(exc), status_code=exc.status_code)
    except MarketplaceAuthError as exc:
        return _a2a_error_response(code=exc.code, message=str(exc), status_code=exc.status_code)
    except FVSClientError as exc:
        return _a2a_error_response(code="fvs_error", message=str(exc), status_code=502)


@mcp.custom_route("/tasks/{task_id}", methods=["GET"], include_in_schema=False)
async def a2a_get_task(request: Request) -> Response:
    task_id = str(request.path_params.get("task_id") or "").strip()
    try:
        if task_id.startswith("quote_"):
            claim_token = _a2a_claim_token_from_request(request)
            if not claim_token:
                return _a2a_error_response(
                    code="unauthenticated",
                    message="Paid A2A task status requires claim_token or X-FVS-Claim-Token.",
                    status_code=401,
                )
            result = await asyncio.to_thread(
                fvs_get_paid_render_status,
                task_id,
                claim_token,
                None,
                None,
            )
            task = build_task_from_paid_quote_response(result)
            _attach_a2a_billing_metadata(task, BILLING_MODE_PAY_PER_RENDER, marketplace_credential=None)
            return JSONResponse({"task": task}, headers={"Cache-Control": "no-store"})

        api_key, marketplace_credential = _a2a_agent_key_or_marketplace(request.headers)
        if not api_key:
            return _a2a_error_response(
                code="unauthenticated",
                message="A2A task status requires X-FVS-Agent-Key or a linked marketplace account.",
                status_code=401,
            )
        result = await asyncio.to_thread(
            fvs_get_render_status,
            task_id,
            None,
            None,
            api_key,
            None,
        )
        task = build_task_from_render_response(result)
        _attach_a2a_billing_metadata(
            task,
            BILLING_MODE_MARKETPLACE if marketplace_credential else BILLING_MODE_ACCOUNT,
            marketplace_credential=marketplace_credential,
        )
        return JSONResponse({"task": task})
    except A2ARequestError as exc:
        return _a2a_error_response(code=exc.code, message=str(exc), status_code=exc.status_code)
    except MarketplaceAuthError as exc:
        return _a2a_error_response(code=exc.code, message=str(exc), status_code=exc.status_code)
    except FVSClientError as exc:
        return _a2a_error_response(code="fvs_error", message=str(exc), status_code=502)


@mcp.custom_route("/tasks/{task_id}:cancel", methods=["POST"], include_in_schema=False)
async def a2a_cancel_task(request: Request) -> Response:
    task_id = str(request.path_params.get("task_id") or "").strip()
    try:
        if task_id.startswith("quote_"):
            return _a2a_error_response(
                code="unsupported_operation",
                message="Paid quote tasks cannot be canceled through A2A before payment; allow the quote to expire.",
                status_code=400,
            )
        api_key, marketplace_credential = _a2a_agent_key_or_marketplace(request.headers)
        if not api_key:
            return _a2a_error_response(
                code="unauthenticated",
                message="A2A task cancellation requires X-FVS-Agent-Key or a linked marketplace account.",
                status_code=401,
            )
        result = await asyncio.to_thread(
            fvs_cancel_render,
            task_id,
            None,
            None,
            api_key,
            None,
        )
        task = build_task_from_render_response(result)
        _attach_a2a_billing_metadata(
            task,
            BILLING_MODE_MARKETPLACE if marketplace_credential else BILLING_MODE_ACCOUNT,
            marketplace_credential=marketplace_credential,
        )
        return JSONResponse({"task": task})
    except A2ARequestError as exc:
        return _a2a_error_response(code=exc.code, message=str(exc), status_code=exc.status_code)
    except MarketplaceAuthError as exc:
        return _a2a_error_response(code=exc.code, message=str(exc), status_code=exc.status_code)
    except FVSClientError as exc:
        return _a2a_error_response(code="fvs_error", message=str(exc), status_code=502)


def _a2a_request_base_url(request: Request) -> str | None:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}"
    host = str(request.headers.get("host") or "").strip()
    if host:
        scheme = str(request.url.scheme or "https").strip() or "https"
        return f"{scheme}://{host}"
    return None


async def _a2a_json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise A2ARequestError("A2A request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise A2ARequestError("A2A request body must be a JSON object.")
    return body


def _a2a_agent_key_or_marketplace(headers: Any) -> tuple[str | None, MarketplaceCredential | None]:
    api_key = agent_api_key_from_headers(headers)
    if api_key:
        return api_key, None
    marketplace_credential = resolve_marketplace_credential(headers)
    if marketplace_credential is not None:
        return marketplace_credential.api_key, marketplace_credential
    return None, None


def _a2a_claim_token_from_request(request: Request) -> str:
    return str(request.query_params.get("claim_token") or request.headers.get("x-fvs-claim-token") or "").strip()


def _a2a_paid_headers(result: dict[str, Any]) -> dict[str, str]:
    headers = {"Cache-Control": "no-store"}
    challenge = str(result.get("www_authenticate") or "").strip()
    if challenge:
        headers["WWW-Authenticate"] = challenge
    return headers


def _a2a_error_response(*, code: str, message: str, status_code: int) -> JSONResponse:
    headers = {"Cache-Control": "no-store"} if status_code in {401, 402, 403} else None
    return JSONResponse(a2a_error_body(code=code, message=message), status_code=status_code, headers=headers)


def _attach_a2a_billing_metadata(
    task: dict[str, Any],
    billing_mode: str,
    *,
    marketplace_credential: MarketplaceCredential | None,
) -> None:
    metadata = task.setdefault("metadata", {}).setdefault("futureVideoStudio", {})
    metadata["billingMode"] = billing_mode
    if marketplace_credential is not None:
        metadata["marketplace"] = credential_metadata(marketplace_credential)


def submit_account_render_with_key(
    *,
    request: dict[str, Any],
    upload_urls: list[str | dict[str, str]] | None,
    api_key: str | None,
) -> dict[str, Any]:
    with downloaded_uploads(upload_urls or []) as url_files:
        return submit_render(
            request_payload=request,
            upload_files=url_files,
            poll_until_complete=False,
            poll_interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
            poll_timeout_seconds=DEFAULT_POLL_TIMEOUT_SECONDS,
            api_key=api_key,
            base_url=None,
        )


@mcp.tool(
    title="Open Future Video Studio",
    description=(
        "Open the Future Video Studio ChatGPT app widget without creating a render. "
        "Use this when the user wants the FVS app panel, wants to paste a status URL, "
        "or wants to prepare a render interactively before spending credits."
    ),
    annotations=ToolAnnotations(
        title="Open Future Video Studio",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    meta=app_tool_meta(invoking="Opening FVS...", invoked="FVS ready"),
)
def fvs_open_chatgpt_app(
    project_id: str | None = None,
    status_url: str | None = None,
    quote_id: str | None = None,
    claim_token: str | None = None,
    final_video_url: str | None = None,
) -> dict[str, Any]:
    """Open the Future Video Studio ChatGPT app widget without side effects."""
    return open_app_snapshot(
        project_id=project_id,
        status_url=status_url,
        quote_id=quote_id,
        claim_token=claim_token,
        final_video_url=final_video_url,
    )


@mcp.tool(
    title="Submit render",
    annotations=ToolAnnotations(
        title="Submit render",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
    meta=app_tool_meta(invoking="Submitting render...", invoked="Render submitted"),
)
def fvs_submit_render(
    request: Annotated[
        RenderRequestPayload,
        Field(
            description=(
                "Required render request object. Include at least name, and usually "
                "project_mode, screenplay, instructions, shot_count, duration, and resolution."
            )
        ),
    ],
    upload_urls: Annotated[
        list[str | UploadUrlPayload] | None,
        Field(
            description=(
                "Optional public HTTPS reference asset URLs. Each object can include "
                "url and filename; filename should match request.assets[].filename."
            )
        ),
    ] = None,
    poll_until_complete: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Submit a Future Video Studio render job through the FVS Agent API.

    Pass the render payload in the required `request` object. For reference
    assets, pass public HTTPS URLs in `upload_urls`; every
    `request.assets[].filename` must match one uploaded URL basename or explicit
    upload URL filename. Credentials come from the connector header,
    marketplace account mapping, or FVS_AGENT_API_KEY in the MCP server
    environment.
    """
    try:
        resolved_api_key = resolve_agent_api_key(api_key=None, ctx=ctx)
        with downloaded_uploads(upload_url_payloads_to_tool_args(upload_urls)) as url_files:
            return submit_render(
                request_payload=render_request_payload_to_dict(request),
                upload_files=url_files,
                poll_until_complete=poll_until_complete,
                poll_interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
                poll_timeout_seconds=DEFAULT_POLL_TIMEOUT_SECONDS,
                api_key=resolved_api_key,
                base_url=None,
            )
    except FVSClientError as exc:
        return error_response(exc)


@mcp.tool(
    title="Create paid render quote",
    annotations=ToolAnnotations(
        title="Create paid render quote",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
    meta=app_tool_meta(invoking="Creating quote...", invoked="Quote ready"),
)
def fvs_create_paid_render_quote(
    request: Annotated[
        RenderRequestPayload,
        Field(
            description=(
                "Required render request object used for the paid quote. Include at "
                "least name, and usually project_mode, screenplay, instructions, "
                "shot_count, duration, and resolution."
            )
        ),
    ],
    upload_urls: Annotated[
        list[str | UploadUrlPayload] | None,
        Field(
            description=(
                "Optional public HTTPS reference asset URLs. Each object can include "
                "url and filename; filename should match request.assets[].filename."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Create a no-account Link payment quote for an FVS render.

    The backend returns HTTP 402 payment details as data: `payment_url`,
    `status_url`, `claim_token`, `amount_cents`, `currency`, and a raw
    `www_authenticate` challenge. Pay `payment_url` with Link's MPP flow, then
    poll with fvs_get_paid_render_status. Local file uploads are not available
    in paid quote mode; use public HTTPS `upload_urls` when assets are needed.
    """
    try:
        return create_paid_render_quote(
            request_payload=render_request_payload_to_dict(request),
            upload_urls=upload_url_payloads_to_tool_args(upload_urls),
            base_url=None,
        )
    except FVSClientError as exc:
        return error_response(exc)


@mcp.tool(
    title="Get render status",
    annotations=ToolAnnotations(
        title="Get render status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    meta=app_tool_meta(invoking="Checking render...", invoked="Status updated"),
)
def fvs_get_render_status(
    project_id: str | None = None,
    status_url: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Check a Future Video Studio render job.

    Provide either `project_id` or the full `status_url` returned by
    fvs_submit_render.
    """
    try:
        return get_render_status(
            project_id=project_id,
            status_url=status_url,
            api_key=resolve_agent_api_key(api_key=None, ctx=ctx),
            base_url=None,
        )
    except FVSClientError as exc:
        return error_response(exc)


@mcp.tool(
    title="Get paid render status",
    annotations=ToolAnnotations(
        title="Get paid render status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    meta=app_tool_meta(invoking="Checking paid render...", invoked="Status updated"),
)
def fvs_get_paid_render_status(
    quote_id: str | None = None,
    claim_token: str | None = None,
    status_url: str | None = None,
) -> dict[str, Any]:
    """Check a no-account paid render created with fvs_create_paid_render_quote.

    Provide the full `status_url` or pass both `quote_id` and `claim_token`.
    """
    try:
        return get_paid_render_status(
            quote_id=quote_id,
            claim_token=claim_token,
            status_url=status_url,
            base_url=None,
        )
    except FVSClientError as exc:
        return error_response(exc)


@mcp.tool(
    title="Cancel render",
    annotations=ToolAnnotations(
        title="Cancel render",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
    meta=app_tool_meta(invoking="Canceling render...", invoked="Render canceled"),
)
def fvs_cancel_render(
    project_id: str | None = None,
    cancel_url: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Cancel a Future Video Studio render job.

    Provide either `project_id` or the full `cancel_url` returned by
    fvs_submit_render.
    """
    try:
        return cancel_render(
            project_id=project_id,
            cancel_url=cancel_url,
            api_key=resolve_agent_api_key(api_key=None, ctx=ctx),
            base_url=None,
        )
    except FVSClientError as exc:
        return error_response(exc)


@mcp.tool(
    title="Download final video",
    description=FINAL_VIDEO_DOWNLOAD_DESCRIPTION,
    annotations=ToolAnnotations(
        title="Download final video",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
    meta=app_tool_meta(invoking="Downloading video...", invoked="Video downloaded"),
)
def fvs_download_final_video(
    final_video_url: Annotated[
        str,
        Field(
            description=(
                "HTTPS signed final_video_url returned by a completed "
                "fvs_get_render_status or fvs_get_paid_render_status response. "
                "Use a fresh status check if the signed URL has expired."
            )
        ),
    ],
    output_path: Annotated[
        str,
        Field(
            description=(
                "Local filesystem path where the MCP server should write the video, "
                "for example C:/Users/me/Videos/fvs-result.mp4 or /tmp/fvs-result.mp4. "
                "Parent directories are created. Existing files are refused unless "
                "overwrite is true."
            )
        ),
    ],
    overwrite: Annotated[
        bool,
        Field(
            description=(
                "Set true only when replacing an existing output_path is intended. "
                "Defaults to false to avoid accidental local file overwrites."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Download a completed Future Video Studio final video to a local file."""
    try:
        return download_final_video(
            final_video_url=final_video_url,
            output_path=output_path,
            overwrite=overwrite,
        )
    except FVSClientError as exc:
        return error_response(exc)


@mcp.tool(
    title="Example render request",
    annotations=ToolAnnotations(
        title="Example render request",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    meta=app_tool_meta(invoking="Loading example...", invoked="Example ready"),
)
def fvs_example_render_request() -> dict[str, Any]:
    """Return a minimal scene render request agents can adapt."""
    return {
        "name": "Archive corridor test",
        "project_mode": "scene",
        "screenplay": (
            "Shot 1: A woman enters a glowing archive corridor lined with suspended photographs. "
            "Shot 2: She reaches toward one moving photograph and the corridor bends into a luminous tunnel. "
            "Shot 3: She steps through into a sunlit memory chamber as photographs orbit overhead."
        ),
        "instructions": (
            "Create exactly three cinematic shots totaling about 24 seconds. "
            "Keep the subject visually consistent. No subtitles or text overlays."
        ),
        "shot_count": 3,
        "scene_target_duration_seconds": 24,
        "visual_style_preset": "realistic_cinematic",
        "video_resolution": "720p",
    }


def render_request_payload_to_dict(request: RenderRequestPayload | dict[str, Any]) -> dict[str, Any]:
    if isinstance(request, RenderRequestPayload):
        return request.model_dump(exclude_none=True)
    return dict(request)


def upload_url_payloads_to_tool_args(
    upload_urls: list[str | UploadUrlPayload | dict[str, str]] | None,
) -> list[str | dict[str, str]]:
    normalized: list[str | dict[str, str]] = []
    for item in upload_urls or []:
        if isinstance(item, str):
            normalized.append(item)
        elif isinstance(item, UploadUrlPayload):
            normalized.append(item.model_dump(exclude_none=True))
        else:
            normalized.append(dict(item))
    return normalized


def error_response(exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(exc),
    }


def resolve_agent_api_key(*, api_key: str | None, ctx: Context | None) -> str | None:
    explicit = str(api_key or "").strip()
    if explicit:
        return explicit
    header_value = agent_api_key_from_context(ctx)
    if header_value:
        return header_value
    marketplace_value = marketplace_agent_api_key_from_context(ctx)
    if marketplace_value:
        return marketplace_value
    env_value = os.getenv("FVS_AGENT_API_KEY", "").strip()
    if env_value:
        return env_value
    return None


def agent_api_key_from_context(ctx: Context | None) -> str | None:
    if ctx is None:
        return None
    try:
        request = ctx.request_context.request
    except Exception:
        return None
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    for header_name in ("x-fvs-agent-key", "x-agent-api-key"):
        value = str(headers.get(header_name) or "").strip()
        if value:
            return value
    auth_header = str(headers.get("authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            return token
    return None


def marketplace_agent_api_key_from_context(ctx: Context | None) -> str | None:
    if ctx is None:
        return None
    try:
        request = ctx.request_context.request
    except Exception:
        return None
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    try:
        credential = resolve_marketplace_credential(headers)
    except MarketplaceAuthError as exc:
        raise FVSClientError(str(exc)) from exc
    return credential.api_key if credential is not None else None


class downloaded_uploads:
    def __init__(self, upload_urls: list[str | dict[str, str]]) -> None:
        self.upload_urls = upload_urls
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.paths: list[str] = []

    def __enter__(self) -> list[str]:
        if not self.upload_urls:
            return []
        self.temp_dir = tempfile.TemporaryDirectory(prefix="fvs-mcp-uploads-")
        base_dir = self.temp_dir.name
        for index, item in enumerate(self.upload_urls, start=1):
            url, filename = normalize_upload_url_item(item, index)
            assert_public_https_url(url)
            path = os.path.join(base_dir, filename)
            download_url_to_path(url, path)
            self.paths.append(path)
        return self.paths

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()
        return False


def normalize_upload_url_item(item: str | dict[str, str], index: int) -> tuple[str, str]:
    if isinstance(item, str):
        url = item.strip()
        filename = filename_from_url(url, index)
        return url, filename
    if isinstance(item, dict):
        url = str(item.get("url") or "").strip()
        filename = str(item.get("filename") or "").strip() or filename_from_url(url, index)
        return url, sanitize_filename(filename, index)
    raise FVSClientError("upload_urls entries must be strings or objects with url and optional filename.")


def filename_from_url(url: str, index: int) -> str:
    parsed = urllib.parse.urlparse(url)
    candidate = os.path.basename(parsed.path or "").strip()
    if not candidate:
        candidate = f"upload-{index}.bin"
    return sanitize_filename(candidate, index)


def sanitize_filename(filename: str, index: int) -> str:
    keep = []
    for char in filename:
        if char.isalnum() or char in {".", "-", "_"}:
            keep.append(char)
        else:
            keep.append("_")
    cleaned = "".join(keep).strip("._")
    return cleaned or f"upload-{index}.bin"


def assert_public_https_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise FVSClientError("upload_urls must use public https URLs.")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        raise FVSClientError("upload_urls cannot target localhost.")
    try:
        addresses = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FVSClientError(f"Unable to resolve upload URL host {hostname}: {exc}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise FVSClientError("upload_urls must resolve to public IP addresses.")


def download_url_to_path(url: str, output_path: str) -> None:
    limit = int_env("FVS_MCP_MAX_UPLOAD_BYTES", MAX_REMOTE_UPLOAD_BYTES)
    request = urllib.request.Request(url, headers={"User-Agent": "future-video-studio-mcp/0.1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > limit:
                raise FVSClientError("Remote upload exceeds FVS_MCP_MAX_UPLOAD_BYTES.")
            total = 0
            with open(output_path, "wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise FVSClientError("Remote upload exceeds FVS_MCP_MAX_UPLOAD_BYTES.")
                    handle.write(chunk)
    except urllib.error.HTTPError as exc:
        raise FVSClientError(f"HTTP {exc.code} while downloading upload URL {url}") from exc
    except urllib.error.URLError as exc:
        raise FVSClientError(f"Network error while downloading upload URL {url}: {exc.reason}") from exc


def main() -> None:
    if "--describe" in sys.argv:
        print(
            json.dumps(
                {
                    "name": "future-video-studio",
                    "transport": "stdio",
                    "default_base_url": DEFAULT_BASE_URL,
                    "tools": [
                        "fvs_open_chatgpt_app",
                        "fvs_submit_render",
                        "fvs_create_paid_render_quote",
                        "fvs_get_render_status",
                        "fvs_get_paid_render_status",
                        "fvs_cancel_render",
                        "fvs_download_final_video",
                        "fvs_example_render_request",
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    transport = os.getenv("FVS_MCP_TRANSPORT", "stdio").strip() or "stdio"
    if "--transport" in sys.argv:
        index = sys.argv.index("--transport")
        if index + 1 >= len(sys.argv):
            raise SystemExit("--transport requires stdio, sse, or streamable-http")
        transport = sys.argv[index + 1]
    mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
