# Contributing to ModelBridge

欢迎中文或英文 Issue / PR。优先欢迎真实兼容性记录、安装恢复问题和可复现的缺陷修复。

## Local development

Use Python 3.12 and Node.js 22.12+ (Node 22 LTS is used in CI):

```sh
git clone https://github.com/leenlbma/modelbridge.git
cd modelbridge
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
npm ci
.venv/bin/python -m unittest discover -s tests -v
npm test
python3 scripts/check_public.py
```

Linux can run the protocol and mocked installer tests. Installing the actual desktop service requires macOS. For the browser UI, run `.venv/bin/python -m bridge.server --data-dir .test-state --port 18780`. The console token is in `.test-state/admin.json`; use it locally as `http://127.0.0.1:18780/#token=YOUR_LOCAL_TOKEN`. Never paste that URL or file into an Issue.

`npm start` opens the Electron client and uses an existing installed service. See [desktop development](docs/DESKTOP.md) for isolated app testing and packaging. Tests use temporary directories and fake keys; do not change your daily service for a unit test.

## Pull requests

1. Open an Issue before large changes; small fixes can go straight to a PR.
2. Keep one concrete problem per PR. Explain the trigger, resulting behavior, and validation.
3. Add regression coverage when changing authentication, message conversion, streaming, configuration persistence or service lifecycle.
4. Keep both README languages, the compatibility matrix and changelog aligned with behavior. Do not mark a provider verified based only on mocks or HTTP 200.
5. Run Python tests, Node tests, the public-file check and the source build. For macOS packaging changes, also build and verify the app.

## Privacy and compatibility evidence

Do not submit keys, headers, personal paths, company endpoints, prompts, source files from user projects, installation records or unreviewed screenshots. Diagnostics should be inspected before sharing. A provider report should include provider/model, protocol, ModelBridge and Claude versions, OS, checks performed and sanitized errors. Report gateway checks and real Claude file tasks separately. Live checks use your own account and may incur charges; CI never requests a real provider Key.

## Dependencies and licensing

Keep direct versions in `requirements.in` and resolve the complete set into `requirements.lock`; explain why an update is needed. Electron and uv updates must also update their version pins, official checksums and third-party notices. Contributions are accepted under the repository's MIT license. Preserve third-party license notices and do not submit code or assets you cannot license.
