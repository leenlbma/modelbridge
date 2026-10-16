<p align="center"><img src="web/icon.svg" width="88" alt="ModelBridge icon"></p>

# ModelBridge

**Connect your own model provider to Claude Desktop.** An open-source macOS configuration app and local protocol gateway.

[![CI](https://github.com/leenlbma/modelbridge/actions/workflows/ci.yml/badge.svg)](https://github.com/leenlbma/modelbridge/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[中文](README.md) · English · [Preview downloads](https://github.com/leenlbma/modelbridge/releases) · [Issues](https://github.com/leenlbma/modelbridge/issues)

**v0.3.2 preview — Apple Silicon, macOS 14+.** ModelBridge is independent of Anthropic and model providers. The current target is the Code feature in Claude Desktop. A provider preset is not a claim of verified end-to-end compatibility. The app UI is currently Chinese.

## Features

- OpenAI, Anthropic, DeepSeek, OpenRouter and editable custom endpoints.
- Responses, Chat Completions and Anthropic Messages API dialects, with one active provider/model.
- Text, streamed tool arguments and tool-result roundtrip checks before saving; failed checks preserve the active connection.
- Electron desktop app with macOS menus, tray, shortcuts and save dialogs.
- Detect existing services and Python first, resume stopped services, and download only missing components.
- Key-free Claude configuration exports and sanitized diagnostics. The loopback gateway stores a key fingerprint, not the upstream plaintext key.

## Quick start

1. Download the DMG from [Releases](https://github.com/leenlbma/modelbridge/releases), drag ModelBridge to Applications and open it. You need Apple Silicon, macOS 14+, [Claude Desktop](https://claude.com/download) and a provider API Key. Builds are ad-hoc signed, without Developer ID signing or Apple notarization. If blocked, verify the source and allow this app in System Settings → Privacy & Security.
2. Follow the environment checks. A compatible service is reused; a stopped one can be resumed. Initial installation needs a network connection if components are missing; no preinstalled Python or Node is required.
3. Select a provider and enter the Key and model ID. For custom providers, supply the API base URL, without `/responses`, `/chat/completions` or `/messages`.
4. Validate and save. Verification uses a small amount of provider credit, has a 180-second timeout and can be cancelled. Active requests prevent switching connections.
5. Export the configuration from the Claude connection page. In Claude, enable **Help → Troubleshooting → Enable Developer Mode**, then choose **Developer → Configure Third-Party Inference… → Import configuration**. Enter the same Key and select **Test connection → Apply Changes → Save & Restart**. Menu availability may vary by Claude version.
6. Start a Code task. A successful client request in ModelBridge confirms the client is using the gateway.

After changing providers or models, revalidate, update Claude's configuration and start a new conversation. Closing the app does not stop the service. The status page can disable the service while retaining data. Multi-connection storage, Keychain integration and automatic updates are not implemented yet.

## Compatibility and privacy

All three protocols have mock HTTP regression coverage using the actual message adapters. Official OpenAI, Anthropic, DeepSeek and OpenRouter accounts have **not** completed current-version gateway and real Claude file-task acceptance. See [the matrix](docs/COMPATIBILITY.md) and [validation scope](docs/VALIDATION.md).

Claude Design, Cowork, images, attachments, long context and the Claude Code CLI are not independently validated. Third-party inference does not grant official account entitlements. Azure-specific authentication, cloud signing, keyless providers, multi-provider routing and automatic failover are not supported. Token counts are estimates.

LiteLLM supplies message conversion; ModelBridge sends HTTP requests to the saved endpoint. The Electron renderer is sandboxed; administration credentials remain in the main process. The gateway listens only on loopback and validates Host, Origin and authentication. Prompts and tool content go to your selected provider. Claude separately manages its configured Key. See [SECURITY.md](SECURITY.md).

Fresh installs use `~/Library/Application Support/ModelBridge/`. Existing installations retain their directory and service identifier. Explicit schema-2 connections retain their endpoint; early schema-1 profiles without one require reconfiguration, preserving the original file until a new connection validates.

## Development

Use Python 3.12 and Node 22.12+:

```sh
git clone https://github.com/leenlbma/modelbridge.git
cd modelbridge
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
npm ci
.venv/bin/python -m unittest discover -s tests -v
npm test
python3 scripts/check_public.py
python3 scripts/build_release.py
```

On an Apple Silicon Mac, `python3 scripts/build_desktop.py --dmg` builds the app and downloads checksum-pinned Electron/uv when missing. `npm start` uses an existing installed backend. See [CONTRIBUTING.md](CONTRIBUTING.md), [desktop notes](docs/DESKTOP.md) and [release procedure](docs/RELEASING.md).

The immediate priorities are real-provider Code task acceptance, clean-Mac installation/login testing, signing and notarization, followed by multiple connections and Keychain support. Cross-platform distribution is outside this preview's scope.

## License

[MIT](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for dependency and binary distribution licenses.
