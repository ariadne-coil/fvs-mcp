# Future Video Studio MCP

<!-- mcp-name: video.future/future-video-studio -->

Future Video Studio MCP lets agents create, monitor, cancel, and download
cinematic AI video renders through the Future Video Studio Agent API.

The hosted MCP server is available at:

```text
https://mcp.future.video/mcp
```

The same server can also run locally over stdio from this Python package.

The hosted service also serves the ChatGPT Apps domain verification endpoint at
`https://mcp.future.video/.well-known/openai-apps-challenge`. Set
`OPENAI_APPS_CHALLENGE_RESPONSE` only if the submission dashboard requires an
exact challenge body.

## ChatGPT App

The hosted MCP server is also ready to connect as a ChatGPT App in developer
mode:

```text
Connector URL: https://mcp.future.video/mcp
App manifest:  https://mcp.future.video/chatgpt-app.json
Widget URI:    ui://future-video-studio/render-console-v1.html
```

The app exposes a small render console inside ChatGPT. It can prepare a render,
submit account-wallet jobs, create no-account Link pay-per-render quotes, poll
status, show payment links, and open the finished video when FVS returns a
signed `final_video_url`.

ChatGPT connector setup:

- Account mode: add `X-FVS-Agent-Key` as a secret connector header.
- Pay-per-render mode: leave `X-FVS-Agent-Key` unset, or set
  `X-FVS-Billing-Mode: pay-per-render`.
- Marketplace-linked mode: use the signed `X-FVS-Marketplace-*` headers from
  the account-linking gateway.

In ChatGPT, enable developer mode, create a connector, and use
`https://mcp.future.video/mcp` as the connector URL. The tool metadata links the
render tools to the FVS widget using the MCP Apps `ui.resourceUri` field and the
ChatGPT `openai/outputTemplate` compatibility field.

## A2A / Gemini Enterprise Preview

The hosted service also exposes a first A2A-compatible wrapper for the Google
Cloud Marketplace / Gemini Enterprise Track 3 path:

```text
https://mcp.future.video/.well-known/agent-card.json
https://mcp.future.video/message:send
https://mcp.future.video/tasks/{task_id}
https://mcp.future.video/tasks/{task_id}:cancel
```

The A2A layer uses the same `X-FVS-Agent-Key` account credential as the MCP
server, marketplace-linked account headers, or paid quote mode. A `message:send`
request can include either plain text brief parts or a structured
`fvs_render_request` data part. Account and marketplace submissions create an
FVS Agent API render and return an A2A task whose `id` is the FVS `project_id`.
Paid quote submissions return HTTP 402 with an A2A task whose `id` is the
`quote_id`; pay the returned Link/Stripe MPP `payment_url`, then poll
`/tasks/{quote_id}?claim_token=...`.

The Agent Card advertises the Official MCP Registry name,
`video.future/future-video-studio`, so enterprise agents can see both the A2A
delegation surface and the hosted MCP tool surface.

## Billing Paths

- Account mode: provide `X-FVS-Agent-Key` or `FVS_AGENT_API_KEY`. Renders charge
  the owning Future Video Studio account wallet under the same credit model and
  saved pipeline defaults as the web app.
- Marketplace-linked mode: provide `X-FVS-Marketplace-Account` for a linked
  marketplace customer. Production deployments should also set
  `X-FVS-Marketplace-Entitlement`, `X-FVS-Marketplace-Timestamp`, and
  `X-FVS-Marketplace-Signature` from the account-linking gateway. The MCP server
  maps the marketplace account to a stored FVS Agent API key and charges the
  linked wallet.
- Pay-per-render mode: omit account credentials or set
  `X-FVS-Billing-Mode: pay-per-render`. The server returns a Link/Stripe MPP
  payment quote, then exposes a claim-token status URL so the agent can retrieve
  the result.

Agents should get explicit user approval before submitting wallet-backed
account renders.

## Security Configuration

Marketplace account mapping is configured with environment variables on the MCP
deployment:

```powershell
$env:FVS_MARKETPLACE_MODE = "optional"
$env:FVS_MARKETPLACE_SHARED_SECRET = "replace-with-random-secret"
$env:FVS_MARKETPLACE_REQUIRE_SIGNATURE = "true"
$env:FVS_MARKETPLACE_ACCOUNT_KEYS_JSON = '{"acct_google_123":{"api_key":"fvs_live_replace_me","entitlement_id":"ent_123","plan":"starter","status":"active"}}'
```

The marketplace signature is HMAC-SHA256 over:

```text
{unix_timestamp}.{marketplace_account_id}.{marketplace_entitlement_id}
```

Send it as `X-FVS-Marketplace-Signature: sha256=<hex>`. The default timestamp
window is 300 seconds and can be adjusted with
`FVS_MARKETPLACE_SIGNATURE_MAX_SKEW_SECONDS`.

For Cloud Run production deploys, pass
`FVS_MARKETPLACE_SHARED_SECRET_SECRET` and
`FVS_MARKETPLACE_ACCOUNT_KEYS_JSON_SECRET` to `scripts/deploy_mcp_cloud.ps1` so
the HMAC secret and account-key mapping come from Secret Manager instead of
plain environment variables.

## Tools

- `fvs_submit_render`: submit a render request, optionally with public asset URLs
- `fvs_create_paid_render_quote`: create a no-account Link payment quote
- `fvs_get_render_status`: check a render by `project_id` or `status_url`
- `fvs_get_paid_render_status`: check a paid render by `status_url` or `quote_id` plus `claim_token`
- `fvs_cancel_render`: cancel a render by `project_id` or `cancel_url`
- `fvs_download_final_video`: save a completed render from its signed URL
- `fvs_example_render_request`: return an example scene render payload
- `fvs_open_chatgpt_app`: open the ChatGPT app widget without creating a render

## Downloading Final Renders

Use `fvs_download_final_video` only after `fvs_get_render_status` or
`fvs_get_paid_render_status` returns a `final_video_url` for a completed render.
If a signed URL has expired, call the relevant status tool again to refresh it.

The download tool:

- performs an unauthenticated HTTPS GET to the signed `final_video_url`
- writes the video bytes to `output_path` on the MCP server's local filesystem
- creates missing parent directories
- refuses to replace an existing file unless `overwrite` is `true`
- does not require `FVS_AGENT_API_KEY`
- does not spend wallet credits, create renders, cancel jobs, or modify remote state
- may take minutes for large videos and uses a 600 second request timeout

Parameters:

- `final_video_url`: an absolute HTTPS signed URL returned by a completed render
  status response. Do not pass arbitrary or untrusted URLs.
- `output_path`: a local path visible to the MCP server process, such as
  `C:/Users/me/Videos/fvs-result.mp4` or `/tmp/fvs-result.mp4`.
- `overwrite`: defaults to `false`; set it to `true` only when replacing the
  destination file is intended.

## Remote MCP Config

Account mode:

```json
{
  "mcpServers": {
    "future-video-studio": {
      "url": "https://mcp.future.video/mcp",
      "headers": {
        "X-FVS-Agent-Key": "fvs_live_replace_me"
      }
    }
  }
}
```

Pay-per-render mode:

```json
{
  "mcpServers": {
    "future-video-studio": {
      "url": "https://mcp.future.video/mcp"
    }
  }
}
```

Marketplace-linked mode:

```json
{
  "mcpServers": {
    "future-video-studio": {
      "url": "https://mcp.future.video/mcp",
      "headers": {
        "X-FVS-Billing-Mode": "marketplace-linked-account",
        "X-FVS-Marketplace-Account": "acct_google_123",
        "X-FVS-Marketplace-Entitlement": "ent_123",
        "X-FVS-Marketplace-Timestamp": "<unix_timestamp>",
        "X-FVS-Marketplace-Signature": "sha256=<hmac>"
      }
    }
  }
}
```

## Local Install

```powershell
python -m pip install future-video-studio-mcp
python -m fvs_mcp_server
```

Example local stdio MCP config:

```json
{
  "mcpServers": {
    "future-video-studio": {
      "command": "python",
      "args": ["-m", "fvs_mcp_server"],
      "env": {
        "FVS_AGENT_API_KEY": "fvs_live_replace_me",
        "FVS_AGENT_BASE_URL": "https://app.future.video"
      }
    }
  }
}
```

`FVS_AGENT_API_KEY` is optional. Leave it unset to use paid quote mode.
`FVS_AGENT_BASE_URL` defaults to `https://app.future.video`.

## Local Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m fvs_mcp_server
```

Run the HTTP transport locally:

```powershell
$env:FVS_MCP_TRANSPORT = "streamable-http"
python -m fvs_mcp_server
```

## Publishing

Build and check the package:

```powershell
python -m build
python -m twine check dist/*
```

This package is intended to publish to PyPI as:

```text
future-video-studio-mcp
```

The MCP Registry package verification marker is the hidden comment near the top
of this README:

```html
<!-- mcp-name: video.future/future-video-studio -->
```

## Links

- Future Video Studio: https://future.video
- Agent API docs: https://future.video/api-docs
- Hosted MCP manifest: https://mcp.future.video/server.json
- Official MCP Registry name: `video.future/future-video-studio`
