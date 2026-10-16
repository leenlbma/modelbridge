# Changelog

## 0.3.2 — 2026-10-17

First public open-source preview.

- Add MIT license, development and security policies, third-party notices, issue templates and CI.
- Publish neutral OpenAI, Anthropic, DeepSeek, OpenRouter and custom-service presets. No private provider endpoint or company-specific model is bundled.
- Preserve explicit schema-2 connections. Old schema-1 profiles without an endpoint require explicit reconfiguration; original files are kept until a new connection passes verification.
- Keep legacy installation directory and launchd identifiers for migration only.
- Add a public-file check and include project licensing in source and desktop packages.
- Document current validation limits rather than claiming all listed providers are verified.

## 0.3.1 — 2026-10-17

Local preview: detect and reuse compatible services and Python environments, restore stopped services without reinstalling, and prefer cached dependencies. Client and backend patch versions may differ.

## 0.3.0 — 2026-10-16

Local preview: Electron macOS client, app-managed environment installation, native menus and launchd service management.

## 0.2.x

Local previews: editable provider presets, three API dialects, macOS-style settings, verification cancellation and timeout, stream validation, and installer recovery.
