from __future__ import annotations

import os
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any


A2A_PROTOCOL_VERSION = "1.0"
DEFAULT_A2A_BASE_URL = "https://mcp.future.video"
DEFAULT_AGENT_API_ORIGIN = "https://app.future.video"
AGENT_CARD_VERSION = "0.1.2"
MCP_REGISTRY_NAME = "video.future/future-video-studio"
MCP_ENDPOINT = "https://mcp.future.video/mcp"
BILLING_MODE_ACCOUNT = "account-wallet"
BILLING_MODE_MARKETPLACE = "marketplace-linked-account"
BILLING_MODE_PAY_PER_RENDER = "pay-per-render"


class A2ARequestError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def configured_a2a_base_url() -> str:
    return normalize_origin(os.getenv("FVS_A2A_BASE_URL") or DEFAULT_A2A_BASE_URL)


def normalize_origin(value: str) -> str:
    cleaned = str(value or "").strip().rstrip("/")
    if not cleaned:
        return DEFAULT_A2A_BASE_URL
    parsed = urllib.parse.urlparse(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return DEFAULT_A2A_BASE_URL
    return f"{parsed.scheme}://{parsed.netloc}"


def build_agent_card(*, base_url: str | None = None, agent_api_origin: str | None = None) -> dict[str, Any]:
    origin = normalize_origin(base_url or configured_a2a_base_url())
    api_origin = normalize_origin(agent_api_origin or os.getenv("FVS_AGENT_BASE_URL") or DEFAULT_AGENT_API_ORIGIN)
    return {
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "name": "Future Video Studio",
        "description": (
            "An agentic video-production service that creates cinematic AI video renders "
            "from briefs, scripts, storyboards, and reference assets."
        ),
        "url": origin,
        "iconUrl": "https://future.video/visuals/FutureVideoIcon.png",
        "version": AGENT_CARD_VERSION,
        "provider": {
            "organization": "Future Video Studio",
            "url": "https://future.video",
        },
        "documentationUrl": "https://future.video/api-docs",
        "defaultInputModes": [
            "text/plain",
            "application/json",
            "image/jpeg",
            "image/png",
            "video/mp4",
            "audio/mpeg",
        ],
        "defaultOutputModes": [
            "text/plain",
            "application/json",
            "video/mp4",
        ],
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "securitySchemes": {
            "fvsAgentKey": {
                "type": "apiKey",
                "in": "header",
                "name": "X-FVS-Agent-Key",
                "description": (
                    "Future Video Studio Agent API key from Settings > Agent API Access. "
                    "Marketplace account-linking can provision or map this key per customer."
                ),
            },
            "fvsMarketplaceAccount": {
                "type": "apiKey",
                "in": "header",
                "name": "X-FVS-Marketplace-Account",
                "description": (
                    "Marketplace-linked account identifier. Production deployments also require "
                    "X-FVS-Marketplace-Entitlement, X-FVS-Marketplace-Timestamp, and "
                    "X-FVS-Marketplace-Signature headers from the account-linking gateway."
                ),
            },
        },
        "security": [{"fvsAgentKey": []}, {"fvsMarketplaceAccount": []}],
        "skills": [
            {
                "id": "create-video-render",
                "name": "Create video render",
                "description": (
                    "Create a cinematic video from a creative or business brief, with optional "
                    "shot count, duration, model, resolution, and style controls."
                ),
                "tags": ["video-generation", "creative-production", "marketing-video", "storyboard"],
                "examples": [
                    "Create a 20 second launch video for this product announcement.",
                    "Turn this campaign brief into a cinematic three-shot video.",
                ],
                "inputModes": ["text/plain", "application/json", "image/jpeg", "image/png"],
                "outputModes": ["text/plain", "application/json", "video/mp4"],
            },
            {
                "id": "render-status",
                "name": "Check render status",
                "description": "Poll a delegated FVS render task and return progress plus final video artifacts.",
                "tags": ["video-generation", "task-status", "async-workflow"],
                "examples": ["Check the render status for task proj_api_abc123."],
                "inputModes": ["application/json", "text/plain"],
                "outputModes": ["application/json", "text/plain", "video/mp4"],
            },
            {
                "id": "cancel-render",
                "name": "Cancel render",
                "description": "Cancel an active FVS render task owned by the authenticated agent account.",
                "tags": ["video-generation", "task-management"],
                "examples": ["Cancel the active render task proj_api_abc123."],
                "inputModes": ["application/json", "text/plain"],
                "outputModes": ["application/json", "text/plain"],
            },
        ],
        "metadata": {
            "mcpRegistryName": MCP_REGISTRY_NAME,
            "mcpEndpoint": MCP_ENDPOINT,
            "agentApiOrigin": api_origin,
            "agentApiPrefix": f"{api_origin}/api/agent",
            "marketplaceTrack": "Google for Startups AI Agents Challenge Track 3",
            "billingModes": [BILLING_MODE_ACCOUNT, BILLING_MODE_MARKETPLACE, BILLING_MODE_PAY_PER_RENDER],
            "billingModeHeader": "X-FVS-Billing-Mode",
            "marketplaceHeaders": [
                "X-FVS-Marketplace-Account",
                "X-FVS-Marketplace-Entitlement",
                "X-FVS-Marketplace-Timestamp",
                "X-FVS-Marketplace-Signature",
            ],
        },
    }


def agent_api_key_from_headers(headers: Any) -> str | None:
    for header_name in ("x-fvs-agent-key", "x-agent-api-key"):
        value = _header_value(headers, header_name)
        if value:
            return value
    auth_header = _header_value(headers, "authorization")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            return token
    return None


def billing_mode_from_request(body: Mapping[str, Any], headers: Any) -> str | None:
    header_mode = _header_value(headers, "x-fvs-billing-mode") or _header_value(headers, "x-billing-mode")
    metadata_mode = ""
    if isinstance(body, Mapping):
        message = body.get("message")
        for source in (body.get("metadata"), message.get("metadata") if isinstance(message, Mapping) else None):
            if isinstance(source, Mapping):
                metadata_mode = str(source.get("billing_mode") or source.get("billingMode") or "").strip()
                if metadata_mode:
                    break
        params = body.get("params")
        if not metadata_mode and isinstance(params, Mapping) and isinstance(params.get("metadata"), Mapping):
            metadata_mode = str(params["metadata"].get("billing_mode") or params["metadata"].get("billingMode") or "").strip()
    return normalize_billing_mode(header_mode or metadata_mode)


def normalize_billing_mode(value: str | None) -> str | None:
    cleaned = str(value or "").strip().lower().replace("_", "-")
    if not cleaned:
        return None
    aliases = {
        "account": BILLING_MODE_ACCOUNT,
        "account-wallet": BILLING_MODE_ACCOUNT,
        "wallet": BILLING_MODE_ACCOUNT,
        "marketplace": BILLING_MODE_MARKETPLACE,
        "marketplace-linked": BILLING_MODE_MARKETPLACE,
        "marketplace-linked-account": BILLING_MODE_MARKETPLACE,
        "paid": BILLING_MODE_PAY_PER_RENDER,
        "paid-quote": BILLING_MODE_PAY_PER_RENDER,
        "pay-per-render": BILLING_MODE_PAY_PER_RENDER,
        "link": BILLING_MODE_PAY_PER_RENDER,
        "stripe": BILLING_MODE_PAY_PER_RENDER,
    }
    if cleaned not in aliases:
        raise A2ARequestError(
            "billing_mode must be account-wallet, marketplace-linked-account, or pay-per-render.",
            status_code=400,
            code="invalid_billing_mode",
        )
    return aliases[cleaned]


def _header_value(headers: Any, name: str) -> str:
    try:
        return str(headers.get(name) or "").strip()
    except Exception:
        return ""


def extract_a2a_render_submission(body: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise A2ARequestError("A2A request body must be a JSON object.")
    message = _message_from_body(body)
    metadata = _merged_metadata(body, message)
    data_parts, text_parts, upload_urls = _collect_message_parts(message)

    explicit_request = _find_render_request(metadata, data_parts)
    if explicit_request is not None:
        render_request = dict(explicit_request)
    else:
        brief = "\n\n".join(text_parts).strip()
        if not brief:
            raise A2ARequestError("Provide text or structured fvs_render_request data to create a render.")
        render_request = render_request_from_brief(brief, metadata=metadata)

    if not str(render_request.get("name") or "").strip():
        render_request["name"] = title_from_text(str(render_request.get("screenplay") or "A2A render"))
    render_request.setdefault("project_mode", "scene")

    upload_urls.extend(_upload_urls_from_metadata(metadata))
    upload_urls.extend(_upload_urls_from_data_parts(data_parts))
    return {
        "request": render_request,
        "upload_urls": _dedupe_upload_urls(upload_urls),
        "context_id": str(message.get("contextId") or body.get("contextId") or render_request.get("name") or "").strip()
        or None,
        "message_id": str(message.get("messageId") or body.get("messageId") or "").strip() or None,
    }


def _message_from_body(body: Mapping[str, Any]) -> Mapping[str, Any]:
    message = body.get("message")
    if isinstance(message, Mapping):
        return message
    params = body.get("params")
    if isinstance(params, Mapping) and isinstance(params.get("message"), Mapping):
        return params["message"]
    raise A2ARequestError("A2A request must include a message object.")


def _merged_metadata(body: Mapping[str, Any], message: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in (body.get("metadata"), message.get("metadata")):
        if isinstance(source, Mapping):
            merged.update(source)
    params = body.get("params")
    if isinstance(params, Mapping) and isinstance(params.get("metadata"), Mapping):
        merged.update(params["metadata"])
    return merged


def _collect_message_parts(message: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str], list[Any]]:
    parts = message.get("parts") or []
    if not isinstance(parts, Sequence) or isinstance(parts, (str, bytes)):
        raise A2ARequestError("A2A message.parts must be an array.")
    data_parts: list[Mapping[str, Any]] = []
    text_parts: list[str] = []
    upload_urls: list[Any] = []
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            text_parts.append(text.strip())
        data = part.get("data")
        if isinstance(data, Mapping):
            data_parts.append(data)
        file_part = part.get("file")
        if isinstance(file_part, Mapping):
            file_url = str(file_part.get("uri") or file_part.get("url") or "").strip()
            if file_url:
                upload_urls.append(
                    {
                        "url": file_url,
                        "filename": str(file_part.get("name") or file_part.get("filename") or "").strip(),
                    }
                )
    return data_parts, text_parts, upload_urls


def _find_render_request(metadata: Mapping[str, Any], data_parts: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for source in [metadata, *data_parts]:
        for key in ("fvs_render_request", "render_request", "request"):
            value = source.get(key)
            if isinstance(value, Mapping):
                return value
    return None


def _upload_urls_from_metadata(metadata: Mapping[str, Any]) -> list[Any]:
    upload_urls = metadata.get("upload_urls") or metadata.get("uploadUrls") or []
    return list(upload_urls) if isinstance(upload_urls, Sequence) and not isinstance(upload_urls, (str, bytes)) else []


def _upload_urls_from_data_parts(data_parts: Sequence[Mapping[str, Any]]) -> list[Any]:
    upload_urls: list[Any] = []
    for data in data_parts:
        candidate = data.get("upload_urls") or data.get("uploadUrls") or []
        if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)):
            upload_urls.extend(candidate)
    return upload_urls


def _dedupe_upload_urls(upload_urls: Sequence[Any]) -> list[Any]:
    seen: set[str] = set()
    deduped: list[Any] = []
    for item in upload_urls:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def render_request_from_brief(brief: str, *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    metadata = metadata or {}
    render_request: dict[str, Any] = {
        "name": title_from_text(brief),
        "project_mode": str(metadata.get("project_mode") or metadata.get("projectMode") or "scene"),
        "screenplay": brief,
        "instructions": str(
            metadata.get("instructions")
            or "Create a concise cinematic video from this brief. Avoid text overlays unless explicitly requested."
        ),
        "shot_count": int_from_metadata(metadata, ("shot_count", "shotCount"), default=3, minimum=1, maximum=64),
        "scene_target_duration_seconds": int_from_metadata(
            metadata,
            ("scene_target_duration_seconds", "sceneTargetDurationSeconds", "duration_seconds", "durationSeconds"),
            default=24,
            minimum=4,
            maximum=600,
        ),
        "video_resolution": str(metadata.get("video_resolution") or metadata.get("videoResolution") or "720p"),
    }
    visual_style = metadata.get("visual_style_preset") or metadata.get("visualStylePreset")
    if visual_style:
        render_request["visual_style_preset"] = str(visual_style)
    return render_request


def int_from_metadata(
    metadata: Mapping[str, Any],
    keys: Sequence[str],
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value: Any = None
    for key in keys:
        if key in metadata:
            value = metadata[key]
            break
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def title_from_text(text: str) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if not compact:
        return "A2A video render"
    compact = compact[:80].strip(" .,:;-")
    return compact or "A2A video render"


def build_task_from_render_response(response: Mapping[str, Any], *, context_id: str | None = None) -> dict[str, Any]:
    project_id = str(response.get("project_id") or "").strip()
    if not project_id:
        raise A2ARequestError(str(response.get("error") or "FVS did not return a project_id."), status_code=502, code="fvs_error")
    status = str(response.get("status") or "").strip().lower()
    state = map_fvs_status_to_a2a_state(status, response=response)
    final_video_url = str(response.get("final_video_url") or "").strip()
    artifacts = []
    if final_video_url:
        artifacts.append(
            {
                "artifactId": "final-video",
                "name": "Final video",
                "parts": [
                    {
                        "file": {
                            "uri": final_video_url,
                            "name": f"{project_id}.mp4",
                            "mimeType": "video/mp4",
                        }
                    }
                ],
            }
        )
    return {
        "id": project_id,
        "contextId": context_id or project_id,
        "status": {
            "state": state,
            "message": {
                "role": "ROLE_AGENT",
                "parts": [
                    {
                        "text": task_status_text(project_id=project_id, state=state, response=response),
                    }
                ],
            },
        },
        "artifacts": artifacts,
        "metadata": {
            "futureVideoStudio": {
                "projectId": project_id,
                "status": status or None,
                "currentStage": response.get("current_stage"),
                "isRunning": response.get("is_running"),
                "statusUrl": response.get("status_url"),
                "cancelUrl": response.get("cancel_url"),
                "finalVideoUrl": final_video_url or None,
                "lastError": response.get("last_error"),
                "assetCount": response.get("asset_count"),
                "clipCount": response.get("clip_count"),
            }
        },
    }


def build_task_from_paid_quote_response(response: Mapping[str, Any], *, context_id: str | None = None) -> dict[str, Any]:
    if response.get("project_id"):
        return build_task_from_render_response(response, context_id=context_id)

    quote_id = str(response.get("quote_id") or "").strip()
    if not quote_id:
        raise A2ARequestError(str(response.get("error") or "FVS did not return a quote_id."), status_code=502, code="fvs_error")

    status = str(response.get("status") or "payment_required").strip().lower()
    state = map_paid_quote_status_to_a2a_state(status, response=response)
    payment_url = str(response.get("payment_url") or "").strip()
    status_url = str(response.get("status_url") or "").strip()
    data = {
        "quote_id": quote_id,
        "status": status,
        "payment_required": bool(response.get("payment_required") or state == "TASK_STATE_INPUT_REQUIRED"),
        "payment_url": payment_url or None,
        "status_url": status_url or None,
        "claim_token": str(response.get("claim_token") or "").strip() or None,
        "amount_cents": response.get("amount_cents") or response.get("amount"),
        "currency": response.get("currency"),
        "credits_quoted": response.get("credits_quoted"),
        "expires_at": response.get("expires_at"),
    }
    return {
        "id": quote_id,
        "contextId": context_id or quote_id,
        "status": {
            "state": state,
            "message": {
                "role": "ROLE_AGENT",
                "parts": [
                    {
                        "text": paid_quote_status_text(quote_id=quote_id, state=state, response=response),
                    },
                    {
                        "data": {"futureVideoStudioPayment": data},
                    },
                ],
            },
        },
        "artifacts": [],
        "metadata": {
            "futureVideoStudio": {
                "quoteId": quote_id,
                "status": status,
                "paymentRequired": data["payment_required"],
                "paymentUrl": payment_url or None,
                "statusUrl": status_url or None,
                "claimToken": data["claim_token"],
                "amountCents": data["amount_cents"],
                "currency": data["currency"],
                "creditsQuoted": data["credits_quoted"],
                "expiresAt": data["expires_at"],
                "wwwAuthenticate": response.get("www_authenticate"),
            }
        },
    }


def map_fvs_status_to_a2a_state(status: str, *, response: Mapping[str, Any]) -> str:
    last_error = str(response.get("last_error") or "").lower()
    if "cancel" in last_error:
        return "TASK_STATE_CANCELED"
    if status == "queued":
        return "TASK_STATE_SUBMITTED"
    if status in {"running", "ready"}:
        return "TASK_STATE_WORKING"
    if status == "completed":
        return "TASK_STATE_COMPLETED"
    if status == "failed":
        return "TASK_STATE_FAILED"
    return "TASK_STATE_WORKING" if response.get("is_running") else "TASK_STATE_UNKNOWN"


def map_paid_quote_status_to_a2a_state(status: str, *, response: Mapping[str, Any]) -> str:
    if response.get("payment_required") or status in {"payment_required", "quoted", "requires_payment", "unpaid"}:
        return "TASK_STATE_INPUT_REQUIRED"
    if status in {"paid", "queued"}:
        return "TASK_STATE_SUBMITTED"
    if status in {"running", "ready", "processing"}:
        return "TASK_STATE_WORKING"
    if status == "completed" or response.get("final_video_url"):
        return "TASK_STATE_COMPLETED"
    if status in {"failed", "expired", "canceled", "cancelled"}:
        return "TASK_STATE_FAILED"
    return "TASK_STATE_UNKNOWN"


def task_status_text(*, project_id: str, state: str, response: Mapping[str, Any]) -> str:
    if state == "TASK_STATE_COMPLETED" and response.get("final_video_url"):
        return f"Future Video Studio render {project_id} is complete. The final video artifact is attached."
    if state == "TASK_STATE_FAILED":
        detail = str(response.get("last_error") or "The render failed.").strip()
        return f"Future Video Studio render {project_id} failed: {detail}"
    if state == "TASK_STATE_CANCELED":
        return f"Future Video Studio render {project_id} was canceled."
    return f"Future Video Studio render {project_id} is {str(response.get('status') or 'running')}."


def paid_quote_status_text(*, quote_id: str, state: str, response: Mapping[str, Any]) -> str:
    if state == "TASK_STATE_INPUT_REQUIRED":
        amount = response.get("amount_cents") or response.get("amount")
        currency = str(response.get("currency") or "usd").upper()
        if amount:
            return f"Future Video Studio quote {quote_id} requires payment of {amount} {currency} cents before rendering starts."
        return f"Future Video Studio quote {quote_id} requires payment before rendering starts."
    if state == "TASK_STATE_FAILED":
        return f"Future Video Studio paid quote {quote_id} failed or expired."
    if state == "TASK_STATE_COMPLETED":
        return f"Future Video Studio paid render {quote_id} is complete."
    return f"Future Video Studio paid render {quote_id} is {str(response.get('status') or 'processing')}."


def a2a_error_body(*, code: str, message: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
        }
    }
