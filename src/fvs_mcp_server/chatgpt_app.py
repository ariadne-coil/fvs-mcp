from __future__ import annotations

from typing import Any


APP_WIDGET_URI = "ui://future-video-studio/render-console-v1.html"
APP_RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"
APP_DOMAIN = "https://mcp.future.video"
APP_CONNECT_DOMAINS = ["https://mcp.future.video", "https://app.future.video"]
APP_RESOURCE_DOMAINS = [
    "https://mcp.future.video",
    "https://app.future.video",
    "https://future.video",
]
APP_REDIRECT_DOMAINS = ["https://app.future.video", "https://future.video"]


APP_RESOURCE_META: dict[str, Any] = {
    "ui": {
        "prefersBorder": True,
        "domain": APP_DOMAIN,
        "csp": {
            "connectDomains": APP_CONNECT_DOMAINS,
            "resourceDomains": APP_RESOURCE_DOMAINS,
        },
    },
    "openai/widgetDescription": (
        "Future Video Studio render console for starting, paying for, polling, "
        "and opening AI video productions from ChatGPT."
    ),
    "openai/widgetPrefersBorder": True,
    "openai/widgetCSP": {
        "connect_domains": APP_CONNECT_DOMAINS,
        "resource_domains": APP_RESOURCE_DOMAINS,
        "redirect_domains": APP_REDIRECT_DOMAINS,
    },
    "openai/widgetDomain": APP_DOMAIN,
}


def app_tool_meta(
    *,
    invoking: str,
    invoked: str,
    visibility: list[str] | None = None,
) -> dict[str, Any]:
    """Return Apps SDK metadata linking an MCP tool to the FVS widget."""
    return {
        "ui": {
            "resourceUri": APP_WIDGET_URI,
            "visibility": visibility or ["model", "app"],
        },
        "openai/outputTemplate": APP_WIDGET_URI,
        "openai/widgetAccessible": True,
        "openai/toolInvocation/invoking": invoking,
        "openai/toolInvocation/invoked": invoked,
    }


def chatgpt_app_manifest() -> dict[str, Any]:
    """Return a compact, human-readable manifest for ChatGPT developer setup."""
    return {
        "name": "Future Video Studio",
        "description": "Create cinematic AI video renders from ChatGPT with FVS account billing or Link pay-per-render quotes.",
        "connector_url": "https://mcp.future.video/mcp",
        "widget_resource": APP_WIDGET_URI,
        "widget_mime_type": APP_RESOURCE_MIME_TYPE,
        "auth": {
            "account_wallet": "Configure the connector header X-FVS-Agent-Key with an FVS Agent API key.",
            "pay_per_render": "Leave X-FVS-Agent-Key unset or set X-FVS-Billing-Mode to pay-per-render.",
            "marketplace": "Use the signed X-FVS-Marketplace-* headers from the account-linking gateway.",
        },
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
    }


def open_app_snapshot(
    *,
    project_id: str | None = None,
    status_url: str | None = None,
    quote_id: str | None = None,
    claim_token: str | None = None,
    final_video_url: str | None = None,
) -> dict[str, Any]:
    """Return structured content that can open the ChatGPT app without side effects."""
    return {
        "app": "future-video-studio",
        "status": "ready",
        "message": "Future Video Studio is ready.",
        "project_id": _clean(project_id),
        "status_url": _clean(status_url),
        "quote_id": _clean(quote_id),
        "claim_token": _clean(claim_token),
        "final_video_url": _clean(final_video_url),
        "default_request": {
            "name": "ChatGPT video render",
            "project_mode": "scene",
            "screenplay": "Shot 1: A cinematic establishing shot. Shot 2: A close character moment. Shot 3: A memorable final image.",
            "instructions": "Keep continuity across shots. No subtitles or text overlays.",
            "shot_count": 3,
            "scene_target_duration_seconds": 24,
            "video_resolution": "720p",
        },
    }


def _clean(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


CHATGPT_WIDGET_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Future Video Studio</title>
  <style>
    :root {
      color-scheme: dark light;
      --bg: #090807;
      --panel: #15110c;
      --panel-2: #21190f;
      --text: #f7efe0;
      --muted: #b9a989;
      --line: #6f5a35;
      --gold: #d8b96d;
      --cyan: #77d7e8;
      --bad: #ff6f86;
      --ok: #8de2b5;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    html, body {
      margin: 0;
      min-height: 100%;
      background: var(--bg);
      color: var(--text);
    }
    body {
      padding: 14px;
      box-sizing: border-box;
    }
    main {
      width: 100%;
      max-width: 760px;
      margin: 0 auto;
      border: 1px solid color-mix(in srgb, var(--line) 72%, transparent);
      background: linear-gradient(145deg, var(--panel), #070605 70%);
      border-radius: 8px;
      overflow: hidden;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px;
      border-bottom: 1px solid color-mix(in srgb, var(--line) 52%, transparent);
      background: var(--panel-2);
    }
    h1, h2, p {
      margin: 0;
    }
    h1 {
      font-size: 17px;
      letter-spacing: 0;
      line-height: 1.2;
    }
    .eyebrow {
      color: var(--cyan);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      margin-bottom: 5px;
    }
    .content {
      display: grid;
      gap: 12px;
      padding: 14px;
    }
    .grid {
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    }
    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    input, textarea, select, button {
      font: inherit;
      border-radius: 6px;
      box-sizing: border-box;
    }
    input, textarea, select {
      width: 100%;
      color: var(--text);
      background: #0d0b08;
      border: 1px solid color-mix(in srgb, var(--line) 70%, transparent);
      padding: 9px 10px;
    }
    textarea {
      min-height: 96px;
      resize: vertical;
    }
    button, a.button {
      border: 1px solid color-mix(in srgb, var(--gold) 82%, transparent);
      background: var(--gold);
      color: #171108;
      padding: 9px 12px;
      text-decoration: none;
      text-align: center;
      cursor: pointer;
      font-weight: 700;
    }
    button.secondary, a.button.secondary {
      color: var(--gold);
      background: transparent;
    }
    button:disabled {
      cursor: wait;
      opacity: 0.65;
    }
    .status {
      display: grid;
      gap: 8px;
      border: 1px solid color-mix(in srgb, var(--line) 62%, transparent);
      background: rgba(255, 255, 255, 0.03);
      border-radius: 8px;
      padding: 12px;
    }
    .status strong {
      color: var(--gold);
    }
    .muted {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    video {
      width: 100%;
      max-height: 360px;
      background: black;
      border: 1px solid color-mix(in srgb, var(--line) 70%, transparent);
      border-radius: 8px;
    }
    code {
      overflow-wrap: anywhere;
      color: var(--cyan);
    }
    .error {
      color: var(--bad);
    }
    .ok {
      color: var(--ok);
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg: #f4efe6;
        --panel: #fffaf0;
        --panel-2: #efe4d2;
        --text: #19140d;
        --muted: #66583e;
        --line: #b9954e;
        --gold: #b88725;
        --cyan: #0b7285;
        --bad: #b4233c;
        --ok: #116b46;
      }
      input, textarea, select {
        background: #fffdf7;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <div class="eyebrow">Future Video Studio</div>
        <h1>Direct a cinematic AI video from ChatGPT</h1>
      </div>
      <button class="secondary" id="open-site" type="button">Open FVS</button>
    </header>
    <section class="content">
      <div class="status" id="status">
        <strong>Ready</strong>
        <p class="muted">Use the form for a quick render, or ask ChatGPT to prepare a full FVS request with references, model choices, and shot timing.</p>
      </div>
      <form class="status" id="render-form">
        <div class="grid">
          <label>Name
            <input id="name" autocomplete="off" value="ChatGPT video render">
          </label>
          <label>Billing
            <select id="billing">
              <option value="account">Account wallet</option>
              <option value="paid">Pay per render</option>
            </select>
          </label>
          <label>Shots
            <input id="shots" type="number" min="1" max="64" value="3">
          </label>
          <label>Duration seconds
            <input id="duration" type="number" min="4" max="600" value="24">
          </label>
        </div>
        <label>Screenplay
          <textarea id="screenplay">Shot 1: A cinematic establishing shot. Shot 2: A close character moment. Shot 3: A memorable final image.</textarea>
        </label>
        <label>Instructions
          <textarea id="instructions">Keep continuity across shots. No subtitles or text overlays.</textarea>
        </label>
        <div class="actions">
          <button id="submit" type="submit">Start render</button>
          <button class="secondary" id="example" type="button">Load example</button>
          <button class="secondary" id="refresh" type="button">Refresh status</button>
        </div>
      </form>
      <div id="result" class="status"></div>
    </section>
  </main>
  <script type="module">
    const state = {
      last: null,
      pending: new Map(),
      rpcId: 0,
    };
    const el = (id) => document.getElementById(id);
    const statusEl = el("status");
    const resultEl = el("result");
    const setBusy = (busy) => {
      for (const button of document.querySelectorAll("button")) button.disabled = busy;
    };
    const text = (value) => (value == null ? "" : String(value));
    const esc = (value) => text(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#039;",
    })[char]);
    const notifyHeight = () => {
      window.openai?.notifyIntrinsicHeight?.(document.body.scrollHeight);
    };
    const setStatus = (title, detail, cls = "") => {
      statusEl.innerHTML = `<strong class="${cls}">${esc(title)}</strong><p class="muted">${esc(detail)}</p>`;
      notifyHeight();
    };
    const renderResult = (data) => {
      state.last = data || state.last;
      const current = state.last || {};
      const status = current.status || current.current_stage || "ready";
      const projectId = current.project_id || current.projectId;
      const quoteId = current.quote_id || current.quoteId;
      const paymentUrl = current.payment_url || current.paymentUrl;
      const statusUrl = current.status_url || current.statusUrl;
      const claimToken = current.claim_token || current.claimToken;
      const finalVideoUrl = current.final_video_url || current.finalVideoUrl;
      let html = `<strong>Status: ${esc(status)}</strong>`;
      if (projectId) html += `<p class="muted">Project: <code>${esc(projectId)}</code></p>`;
      if (quoteId) html += `<p class="muted">Quote: <code>${esc(quoteId)}</code></p>`;
      if (current.amount_cents) {
        const dollars = (Number(current.amount_cents) / 100).toFixed(2);
        html += `<p class="muted">Payment due: ${esc((current.currency || "usd").toUpperCase())} $${esc(dollars)}</p>`;
      }
      if (current.error) html += `<p class="error">${esc(current.error)}</p>`;
      if (paymentUrl) {
        html += `<div class="actions"><a class="button" href="${esc(paymentUrl)}" target="_blank" rel="noreferrer">Pay quote</a></div>`;
      }
      if (statusUrl) html += `<p class="muted">Status URL: <code>${esc(statusUrl)}</code></p>`;
      if (claimToken) html += `<p class="muted">Claim token saved for polling.</p>`;
      if (finalVideoUrl) {
        html += `<video controls preload="metadata" src="${esc(finalVideoUrl)}"></video>`;
        html += `<div class="actions"><a class="button secondary" href="${esc(finalVideoUrl)}" target="_blank" rel="noreferrer">Open video</a></div>`;
      }
      resultEl.innerHTML = html;
      notifyHeight();
    };
    const rpcRequest = (method, params) => {
      if (window.openai?.callTool && method === "tools/call") {
        return window.openai.callTool(params.name, params.arguments || {});
      }
      return new Promise((resolve, reject) => {
        const id = ++state.rpcId;
        state.pending.set(id, { resolve, reject });
        window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
      });
    };
    window.addEventListener("message", (event) => {
      if (event.source !== window.parent) return;
      const message = event.data;
      if (!message || message.jsonrpc !== "2.0") return;
      if (message.id && state.pending.has(message.id)) {
        const pending = state.pending.get(message.id);
        state.pending.delete(message.id);
        if (message.error) pending.reject(message.error);
        else pending.resolve(message.result);
        return;
      }
      if (message.method === "ui/notifications/tool-result") {
        renderResult(message.params?.structuredContent || message.params);
      }
      if (message.method === "ui/notifications/tool-input") {
        const input = message.params?.structuredContent || message.params || {};
        if (input.default_request) loadRequest(input.default_request);
      }
    }, { passive: true });
    const callTool = async (name, args) => {
      setBusy(true);
      setStatus("Working", name);
      try {
        const result = await rpcRequest("tools/call", { name, arguments: args || {} });
        const data = result?.structuredContent || result;
        renderResult(data);
        setStatus("Ready", "Tool call complete.", data?.error ? "error" : "ok");
      } catch (error) {
        setStatus("Error", error?.message || JSON.stringify(error), "error");
      } finally {
        setBusy(false);
      }
    };
    const buildRequest = () => ({
      name: el("name").value.trim() || "ChatGPT video render",
      project_mode: "scene",
      screenplay: el("screenplay").value.trim(),
      instructions: el("instructions").value.trim(),
      shot_count: Math.max(1, Math.min(64, Number(el("shots").value || 3))),
      scene_target_duration_seconds: Math.max(4, Math.min(600, Number(el("duration").value || 24))),
      video_resolution: "720p",
    });
    const loadRequest = (request) => {
      el("name").value = request.name || "ChatGPT video render";
      el("screenplay").value = request.screenplay || "";
      el("instructions").value = request.instructions || "";
      el("shots").value = request.shot_count || 3;
      el("duration").value = request.scene_target_duration_seconds || 24;
      notifyHeight();
    };
    el("render-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const request = buildRequest();
      if (el("billing").value === "paid") {
        callTool("fvs_create_paid_render_quote", { request });
      } else {
        callTool("fvs_submit_render", { request });
      }
    });
    el("example").addEventListener("click", async () => {
      setBusy(true);
      try {
        const result = await rpcRequest("tools/call", { name: "fvs_example_render_request", arguments: {} });
        loadRequest(result?.structuredContent || result || {});
      } finally {
        setBusy(false);
      }
    });
    el("refresh").addEventListener("click", () => {
      const current = state.last || {};
      const projectId = current.project_id || current.projectId;
      const statusUrl = current.status_url || current.statusUrl;
      const quoteId = current.quote_id || current.quoteId;
      const claimToken = current.claim_token || current.claimToken;
      if (quoteId || claimToken) {
        callTool("fvs_get_paid_render_status", { quote_id: quoteId, claim_token: claimToken, status_url: statusUrl });
      } else if (projectId || statusUrl) {
        callTool("fvs_get_render_status", { project_id: projectId, status_url: statusUrl });
      } else {
        setStatus("Nothing to refresh", "Start a render or paste a status URL in conversation first.");
      }
    });
    el("open-site").addEventListener("click", () => {
      const href = "https://app.future.video";
      if (window.openai?.openExternal) window.openai.openExternal({ href });
      else window.open(href, "_blank", "noopener,noreferrer");
    });
    renderResult(window.openai?.toolOutput || window.openai?.toolResponseMetadata || {});
    notifyHeight();
  </script>
</body>
</html>
""".strip()
