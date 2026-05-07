from __future__ import annotations

import json
import os
import ipaddress
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
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


INSTRUCTIONS = """
Create videos through the Future Video Studio Agent API.

Use fvs_submit_render for account API-key projects, fvs_create_paid_render_quote
for no-account Link payment quotes, fvs_get_render_status or
fvs_get_paid_render_status to poll, fvs_cancel_render to stop account-owned runs,
and fvs_download_final_video to save a finished signed final_video_url. Prefer
FVS_AGENT_API_KEY and FVS_AGENT_BASE_URL from the MCP server environment when
the user has an FVS account. Agent API keys are owned by normal FVS user
accounts and use the same pricing, wallet, and saved pipeline defaults as the
web app. Paid quotes use the same credit estimate and return a claim token so
the result can be retrieved without an FVS account.
""".strip()

API_REFERENCE = """
# Future Video Studio Agent API

Default app origin: https://app.future.video
Route prefix: /api/agent

Authentication:
- X-FVS-Agent-Key: <agent key> for account wallet mode
- no key for paid quote mode through Link/Stripe MPP

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
    "version": "0.1.2",
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
                }
            ],
        }
    ],
    "packages": [
        {
            "registryType": "pypi",
            "identifier": "future-video-studio-mcp",
            "version": "0.1.2",
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
        }
    )


@mcp.custom_route("/server.json", methods=["GET"], include_in_schema=False)
async def server_manifest(request: Request) -> Response:
    return JSONResponse(SERVER_MANIFEST)


@mcp.custom_route("/.well-known/mcp-server.json", methods=["GET"], include_in_schema=False)
async def well_known_server_manifest(request: Request) -> Response:
    return JSONResponse(SERVER_MANIFEST)


@mcp.tool()
def fvs_submit_render(
    request: dict[str, Any],
    upload_files: list[str] | None = None,
    upload_urls: list[str | dict[str, str]] | None = None,
    poll_until_complete: bool = False,
    base_url: str | None = None,
    api_key: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Submit a Future Video Studio render job through the FVS Agent API.

    Pass the render payload as `request`. For uploads, pass local file paths in
    `upload_files`; every `request.assets[].filename` must match one uploaded
    file basename. Prefer credentials from FVS_AGENT_API_KEY instead of passing
    api_key through the tool call.
    """
    try:
        resolved_api_key = resolve_agent_api_key(api_key=api_key, ctx=ctx)
        with downloaded_uploads(upload_urls or []) as url_files:
            return submit_render(
                request_payload=request,
                upload_files=[*(upload_files or []), *url_files],
                poll_until_complete=poll_until_complete,
                poll_interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
                poll_timeout_seconds=DEFAULT_POLL_TIMEOUT_SECONDS,
                api_key=resolved_api_key,
                base_url=base_url,
            )
    except FVSClientError as exc:
        return error_response(exc)


@mcp.tool()
def fvs_create_paid_render_quote(
    request: dict[str, Any],
    upload_urls: list[str | dict[str, str]] | None = None,
    base_url: str | None = None,
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
            request_payload=request,
            upload_urls=upload_urls or [],
            base_url=base_url,
        )
    except FVSClientError as exc:
        return error_response(exc)


@mcp.tool()
def fvs_get_render_status(
    project_id: str | None = None,
    status_url: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
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
            api_key=resolve_agent_api_key(api_key=api_key, ctx=ctx),
            base_url=base_url,
        )
    except FVSClientError as exc:
        return error_response(exc)


@mcp.tool()
def fvs_get_paid_render_status(
    quote_id: str | None = None,
    claim_token: str | None = None,
    status_url: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Check a no-account paid render created with fvs_create_paid_render_quote.

    Provide the full `status_url` or pass both `quote_id` and `claim_token`.
    """
    try:
        return get_paid_render_status(
            quote_id=quote_id,
            claim_token=claim_token,
            status_url=status_url,
            base_url=base_url,
        )
    except FVSClientError as exc:
        return error_response(exc)


@mcp.tool()
def fvs_cancel_render(
    project_id: str | None = None,
    cancel_url: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
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
            api_key=resolve_agent_api_key(api_key=api_key, ctx=ctx),
            base_url=base_url,
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
        openWorldHint=True,
    ),
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


@mcp.tool()
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
