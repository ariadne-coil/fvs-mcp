from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_BASE_URL = "https://app.future.video"
DEFAULT_POLL_INTERVAL_SECONDS = 15.0
DEFAULT_POLL_TIMEOUT_SECONDS = 1800.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 120.0
API_KEY_ENV = "FVS_AGENT_API_KEY"
BASE_URL_ENV = "FVS_AGENT_BASE_URL"
USER_AGENT = "future-video-studio-mcp/0.1.0"


@dataclass(frozen=True)
class FVSClientConfig:
    api_key: str | None
    agent_base_url: str
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS


class FVSClientError(RuntimeError):
    pass


def make_config(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    require_api_key: bool = True,
) -> FVSClientConfig:
    resolved_api_key = (api_key or os.getenv(API_KEY_ENV) or "").strip()
    if require_api_key and not resolved_api_key:
        raise FVSClientError(
            f"Missing Future Video Studio agent API key. Set {API_KEY_ENV} "
            "in the MCP server environment or pass api_key explicitly."
        )
    return FVSClientConfig(
        api_key=resolved_api_key or None,
        agent_base_url=normalize_agent_base(base_url or os.getenv(BASE_URL_ENV) or DEFAULT_BASE_URL),
        request_timeout_seconds=max(1.0, float(request_timeout_seconds)),
    )


def normalize_agent_base(base_url: str) -> str:
    cleaned = str(base_url or "").strip().rstrip("/")
    if not cleaned:
        raise FVSClientError("Future Video Studio base URL is empty.")
    if cleaned.endswith("/api/agent"):
        return cleaned
    return f"{cleaned}/api/agent"


def detect_upload_mime_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def guess_upload_content_type(path: Path, data: bytes) -> str:
    return detect_upload_mime_type(data) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def submit_render(
    *,
    request_payload: Mapping[str, Any],
    upload_files: Sequence[str | Path] | None = None,
    poll_until_complete: bool = False,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    poll_timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS,
    api_key: str | None = None,
    base_url: str | None = None,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not isinstance(request_payload, Mapping):
        raise FVSClientError("request_payload must be an object.")
    normalized_payload = dict(request_payload)
    if not str(normalized_payload.get("name") or "").strip():
        raise FVSClientError("request_payload.name is required.")

    paths = [Path(path).expanduser() for path in (upload_files or [])]
    for path in paths:
        if not path.is_file():
            raise FVSClientError(f"Upload file not found: {path}")
    validate_asset_filenames(normalized_payload, paths)

    config = make_config(
        api_key=api_key,
        base_url=base_url,
        request_timeout_seconds=request_timeout_seconds,
    )
    request_json = json.dumps(normalized_payload, ensure_ascii=False, separators=(",", ":"))
    body, content_type = encode_multipart(
        fields=[("request_json", request_json)],
        file_fields=[("files", path) for path in paths],
    )
    response = request_json_api(
        url=f"{config.agent_base_url}/renders",
        method="POST",
        config=config,
        body=body,
        content_type=content_type,
    )
    if not poll_until_complete:
        return response
    return poll_render(
        initial_response=response,
        config=config,
        poll_interval_seconds=poll_interval_seconds,
        poll_timeout_seconds=poll_timeout_seconds,
    )


def get_render_status(
    *,
    project_id: str | None = None,
    status_url: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    config = make_config(
        api_key=api_key,
        base_url=base_url,
        request_timeout_seconds=request_timeout_seconds,
    )
    url = resolve_status_url(project_id=project_id, status_url=status_url, agent_base_url=config.agent_base_url)
    return request_json_api(url=url, method="GET", config=config)


def create_paid_render_quote(
    *,
    request_payload: Mapping[str, Any],
    upload_urls: Sequence[str | Mapping[str, str]] | None = None,
    base_url: str | None = None,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not isinstance(request_payload, Mapping):
        raise FVSClientError("request_payload must be an object.")
    normalized_payload = dict(request_payload)
    if not str(normalized_payload.get("name") or "").strip():
        raise FVSClientError("request_payload.name is required.")
    normalized_upload_urls: list[str | dict[str, str]] = []
    for item in upload_urls or []:
        if isinstance(item, str):
            normalized_upload_urls.append(item)
        elif isinstance(item, Mapping):
            url = str(item.get("url") or "").strip()
            filename = str(item.get("filename") or "").strip()
            normalized_upload_urls.append({"url": url, "filename": filename} if filename else {"url": url})
        else:
            raise FVSClientError("upload_urls entries must be strings or objects with url and optional filename.")
    config = make_config(
        base_url=base_url,
        request_timeout_seconds=request_timeout_seconds,
        require_api_key=False,
    )
    body = json.dumps(
        {"request": normalized_payload, "upload_urls": normalized_upload_urls},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return request_json_api(
        url=f"{config.agent_base_url}/render-quotes",
        method="POST",
        config=config,
        body=body,
        content_type="application/json",
        allow_payment_required=True,
    )


def get_paid_render_status(
    *,
    quote_id: str | None = None,
    claim_token: str | None = None,
    status_url: str | None = None,
    base_url: str | None = None,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    config = make_config(
        base_url=base_url,
        request_timeout_seconds=request_timeout_seconds,
        require_api_key=False,
    )
    url = resolve_paid_status_url(
        quote_id=quote_id,
        claim_token=claim_token,
        status_url=status_url,
        agent_base_url=config.agent_base_url,
    )
    return request_json_api(url=url, method="GET", config=config)


def cancel_render(
    *,
    project_id: str | None = None,
    cancel_url: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    config = make_config(
        api_key=api_key,
        base_url=base_url,
        request_timeout_seconds=request_timeout_seconds,
    )
    url = resolve_cancel_url(project_id=project_id, cancel_url=cancel_url, agent_base_url=config.agent_base_url)
    return request_json_api(url=url, method="POST", config=config)


def download_final_video(
    *,
    final_video_url: str,
    output_path: str | Path,
    request_timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    url = str(final_video_url or "").strip()
    if not url:
        raise FVSClientError("final_video_url is required.")
    destination = Path(output_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url=url, headers={"User-Agent": USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(request_timeout_seconds))) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        detail = extract_error_detail(exc.read())
        raise FVSClientError(f"HTTP {exc.code} while downloading final video: {detail}") from exc
    except urllib.error.URLError as exc:
        raise FVSClientError(f"Network error while downloading final video: {exc.reason}") from exc
    destination.write_bytes(data)
    return {
        "output_path": str(destination),
        "bytes_written": len(data),
    }


def resolve_status_url(
    *,
    project_id: str | None,
    status_url: str | None,
    agent_base_url: str,
) -> str:
    if status_url and str(status_url).strip():
        return str(status_url).strip()
    if project_id and str(project_id).strip():
        return f"{agent_base_url}/renders/{str(project_id).strip()}"
    raise FVSClientError("Provide project_id or status_url.")


def resolve_cancel_url(
    *,
    project_id: str | None,
    cancel_url: str | None,
    agent_base_url: str,
) -> str:
    if cancel_url and str(cancel_url).strip():
        return str(cancel_url).strip()
    if project_id and str(project_id).strip():
        return f"{agent_base_url}/renders/{str(project_id).strip()}/cancel"
    raise FVSClientError("Provide project_id or cancel_url.")


def resolve_paid_status_url(
    *,
    quote_id: str | None,
    claim_token: str | None,
    status_url: str | None,
    agent_base_url: str,
) -> str:
    if status_url and str(status_url).strip():
        return str(status_url).strip()
    normalized_quote_id = str(quote_id or "").strip()
    normalized_claim_token = str(claim_token or "").strip()
    if not normalized_quote_id or not normalized_claim_token:
        raise FVSClientError("Provide status_url or both quote_id and claim_token.")
    query = urllib.parse.urlencode({"claim_token": normalized_claim_token})
    return f"{agent_base_url}/paid-renders/{normalized_quote_id}?{query}"


def validate_asset_filenames(payload: Mapping[str, Any], file_paths: Sequence[Path]) -> None:
    assets = payload.get("assets")
    if not assets:
        return
    if not isinstance(assets, Sequence) or isinstance(assets, (str, bytes)):
        raise FVSClientError("request_payload.assets must be an array when provided.")
    upload_names = {path.name for path in file_paths}
    missing: list[str] = []
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        filename = str(asset.get("filename") or "").strip()
        if filename and filename not in upload_names:
            missing.append(filename)
    if missing:
        raise FVSClientError(
            "Every assets[].filename value must match an uploaded file basename. "
            f"Missing uploads for: {', '.join(sorted(missing))}"
        )


def encode_multipart(
    *,
    fields: Iterable[tuple[str, str]],
    file_fields: Iterable[tuple[str, Path]],
) -> tuple[bytes, str]:
    boundary = f"----fvs-mcp-{uuid.uuid4().hex}"
    lines: list[bytes] = []
    for name, value in fields:
        lines.extend(
            [
                f"--{boundary}".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"),
                b"",
                value.encode("utf-8"),
            ]
        )
    for field_name, path in file_fields:
        data = path.read_bytes()
        content_type = guess_upload_content_type(path, data)
        lines.extend(
            [
                f"--{boundary}".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{path.name}"'
                ).encode("utf-8"),
                f"Content-Type: {content_type}".encode("utf-8"),
                b"",
                data,
            ]
        )
    lines.append(f"--{boundary}--".encode("utf-8"))
    lines.append(b"")
    return b"\r\n".join(lines), f"multipart/form-data; boundary={boundary}"


def request_json_api(
    *,
    url: str,
    method: str,
    config: FVSClientConfig,
    body: bytes | None = None,
    content_type: str | None = None,
    allow_payment_required: bool = False,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if config.api_key:
        headers["X-FVS-Agent-Key"] = config.api_key
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=config.request_timeout_seconds) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw_error = exc.read()
        if exc.code == 402 and allow_payment_required:
            return parse_json_response(
                raw_error,
                url=url,
                extra={
                    "ok": False,
                    "payment_required": True,
                    "www_authenticate": exc.headers.get("WWW-Authenticate"),
                },
            )
        detail = extract_error_detail(raw_error)
        raise FVSClientError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise FVSClientError(f"Network error while calling {url}: {exc.reason}") from exc
    return parse_json_response(raw, url=url)


def parse_json_response(raw: bytes, *, url: str, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise FVSClientError(f"Non-JSON response from {url}.") from exc
    if not isinstance(parsed, dict):
        raise FVSClientError(f"Unexpected non-object JSON response from {url}.")
    if extra:
        return {**dict(extra), **parsed}
    return parsed


def extract_error_detail(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return "empty error response"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, Mapping):
        detail = parsed.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return text


def poll_render(
    *,
    initial_response: Mapping[str, Any],
    config: FVSClientConfig,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    poll_timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    response = dict(initial_response)
    status_url = str(response.get("status_url") or "").strip()
    if not status_url and response.get("project_id"):
        status_url = f"{config.agent_base_url}/renders/{response['project_id']}"
    if not status_url:
        return response
    interval = max(1.0, float(poll_interval_seconds))
    deadline = time.time() + max(1.0, float(poll_timeout_seconds))
    while time.time() < deadline:
        if is_terminal_response(response):
            return response
        time.sleep(interval)
        response = request_json_api(url=status_url, method="GET", config=config)
    raise FVSClientError("Poll timeout exceeded before the render reached a terminal state.")


def is_terminal_response(response: Mapping[str, Any]) -> bool:
    status = str(response.get("status") or "").strip().lower()
    current_stage = str(response.get("current_stage") or "").strip().lower()
    is_running = bool(response.get("is_running"))
    if status in {"completed", "failed"}:
        return True
    if current_stage == "halted_for_review":
        return True
    return not is_running and status not in {"queued", "running"}
