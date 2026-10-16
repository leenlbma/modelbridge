# Third-party notices

ModelBridge's original code and assets are licensed under MIT. Third-party software retains its own licenses. ModelBridge is independent of Anthropic and the listed model providers; their names identify interoperability targets, not endorsement.

## Distributed desktop components

| Component | Version | License / source | Included notice |
| --- | --- | --- | --- |
| Electron | 42.11.15 | [MIT](https://github.com/electron/electron/blob/v42.11.15/LICENSE) | `Contents/Resources/LICENSE` |
| Chromium and Electron dependencies | As distributed with Electron | Multiple licenses | `Contents/Resources/LICENSES.chromium.html` |
| uv | 0.11.21 | [MIT](https://github.com/astral-sh/uv/blob/0.11.21/LICENSE-MIT) or [Apache-2.0](https://github.com/astral-sh/uv/blob/0.11.21/LICENSE-APACHE) | `Contents/Resources/app/bootstrap/uv-LICENSE-MIT` and `uv-LICENSE-APACHE` |

The app includes ModelBridge's `LICENSE` and this document in its application and backend resources. Downloaded Electron and uv archives are checked against the pinned SHA256 values in `desktop/runtime.json` and `desktop/bootstrap.json`.

## Runtime dependencies downloaded on installation

Python and Python packages are installed separately, rather than embedded in the desktop download. Their distributions retain upstream license files and metadata.

| Direct dependency | Version | License / source |
| --- | --- | --- |
| CPython | 3.12.13 | [PSF License](https://docs.python.org/3.12/license.html), via uv managed Python |
| FastAPI | 0.143.2 | [MIT](https://github.com/fastapi/fastapi) |
| Uvicorn | 0.54.0 | [BSD-3-Clause](https://github.com/Kludex/uvicorn) |
| HTTPX | 0.28.1 | [BSD-3-Clause](https://github.com/encode/httpx) |
| LiteLLM | 1.105.1 | [MIT for the imported open-source library](https://github.com/BerriAI/litellm) |

The full transitive version set is recorded in `requirements.lock`. ModelBridge imports LiteLLM's message adapters; it does not bundle or run the LiteLLM proxy or enterprise features. If you redistribute an installed runtime, include the licenses of all bundled packages and Python components as well.
