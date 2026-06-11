# Changelog

## 0.1.6

- Align status and download MCP annotations with actual behavior for ChatGPT app review.
- Assert every reviewer-visible tool annotation in tests and submission metadata.

## 0.1.5

- Add ChatGPT app widget metadata, submission assets, and review-safe test cases.
- Add explicit `RenderRequestPayload` and `UploadUrlPayload` schemas so render tools expose a clear required `request` argument.
- Add A2A endpoints and marketplace-linked account header support.
- Add MCP Registry and ChatGPT app manifest metadata for the hosted server.

## 0.1.2

- Add complete MCP annotations and parameter descriptions for `fvs_download_final_video`.
- Document download side effects, prerequisites, timeout behavior, and alternatives.
- Refuse existing output files by default; require `overwrite=true` to replace them.
- Require final render download URLs to be absolute HTTPS URLs.

## 0.1.1

- Prepare standalone MCP package repository.
- Add PyPI package metadata and official MCP Registry package metadata.
- Document hosted remote MCP and local stdio installation paths.

## 0.1.0

- Initial Future Video Studio MCP server.
