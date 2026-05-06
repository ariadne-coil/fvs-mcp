# Future Video Studio MCP

<!-- mcp-name: video.future/future-video-studio -->

Future Video Studio MCP lets agents create, monitor, cancel, and download
cinematic AI video renders through the Future Video Studio Agent API.

The hosted MCP server is available at:

```text
https://mcp.future.video/mcp
```

The same server can also run locally over stdio from this Python package.

## Billing Paths

- Account mode: provide `X-FVS-Agent-Key` or `FVS_AGENT_API_KEY`. Renders charge
  the owning Future Video Studio account wallet under the same credit model and
  saved pipeline defaults as the web app.
- Pay-per-render mode: omit the account key. The server returns a Link/Stripe
  MPP payment quote, then exposes a claim-token status URL so the agent can
  retrieve the result.

Agents should get explicit user approval before submitting wallet-backed
account renders.

## Tools

- `fvs_submit_render`: submit a render request, optionally with uploaded files
- `fvs_create_paid_render_quote`: create a no-account Link payment quote
- `fvs_get_render_status`: check a render by `project_id` or `status_url`
- `fvs_get_paid_render_status`: check a paid render by `status_url` or `quote_id` plus `claim_token`
- `fvs_cancel_render`: cancel a render by `project_id` or `cancel_url`
- `fvs_download_final_video`: save a completed render from its signed URL
- `fvs_example_render_request`: return an example scene render payload

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
