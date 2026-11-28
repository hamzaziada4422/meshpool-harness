# Third-party notices

Meshpool desktop bundles the open-source software below. Each component stays under its own license. License texts ship inside each component's folder in the app.

## Bundled programs

| Component | License | Source |
|---|---|---|
| Hermes Agent 0.21.2 (Nous Research) | MIT | https://github.com/NousResearch/hermes-agent (small patches in acp_adapter/server.py and tools/approval.py: `packaging/hermes-acp-server.patch`) |
| Pi coding agent (Mario Zechner) | MIT | https://www.npmjs.com/package/@earendil-works/pi-coding-agent |
| Ponytail skills (ponytail, ponytail-review), commit ebd8654 (Dietrich Gebert) | MIT | https://github.com/DietrichGebert/ponytail (app/defaults/skills/ponytail*) |
| Node.js 22 | MIT and bundled licenses | https://nodejs.org |
| uv 0.13.7 (Astral), `uv` and `uvx` taken unmodified from the official PyPI wheels, SHA-256 checked (`uv/` beside the app; pins in `packaging/bundled_tools.py`), runs the uvx MCP servers | MIT or Apache-2.0 | https://github.com/astral-sh/uv |
| Python 3.12 (python-build-standalone) | PSF License and bundled licenses | https://github.com/astral-sh/python-build-standalone |
| Git for Windows, MinGit (Windows) | GPL-2.0 | Source: https://github.com/git-for-windows/git |
| Git 2.56.1, built unmodified from the official source (macOS: `git/`; build script `packaging/mac/build_git.sh`, licence `git/COPYING-git-GPL-2.0.txt`) | GPL-2.0 | Source: https://mirrors.edge.kernel.org/pub/software/scm/git/git-2.56.1.tar.xz |
| LibreOffice 26.2.7, binaries unmodified (unused parts such as help, Base, Math, extensions and other languages are removed: `packaging/lo_prune.py`), used for Office conversion and rendering (`app/vendor/libreoffice`; notice and licence in `app/defaults/licenses/libreoffice`) | MPL-2.0 and bundled licenses | Source: https://downloadarchive.documentfoundation.org/libreoffice/old/26.2.7.3/src/ |
| bubblewrap 0.11.0, built unmodified from the official source and linked statically with libcap 2.76 (Linux: `app/vendor/bwrap`; build script `packaging/linux/build_bwrap.sh`, licences `app/vendor/bwrap/COPYING-bubblewrap-LGPL-2.0.txt` and `LICENSE-libcap.txt`), the command sandbox | LGPL-2.0-or-later (libcap: BSD-3-Clause, one of its two licences) | Source: https://github.com/containers/bubblewrap/releases/download/v0.11.0/bubblewrap-0.11.0.tar.xz and https://mirrors.edge.kernel.org/pub/linux/libs/security/linux-privs/libcap2/libcap-2.76.tar.xz |
| ffmpeg (static build taken unmodified from the imageio-ffmpeg 0.7.0 wheel, `app/vendor/ffmpeg`; FFmpeg 7.0 to 7.1 as configured by its builders, GPL because it includes libx264; build details in `ffmpeg -version`) | GPL-2.0-or-later (the wrapper package is BSD-2-Clause) | Source: https://ffmpeg.org/releases/ and https://github.com/imageio/imageio-binaries |
| pandoc 2.19.2, unmodified, from the pypandoc_binary 1.13 wheel (`app/vendor/pandoc`) | GPL-2.0-or-later | Source: https://github.com/jgm/pandoc/releases/tag/2.19.2 |
| OpenAI Whisper "base" speech model, converted to CTranslate2 format by Systran (`app/vendor/whisper/base`) | MIT | https://huggingface.co/Systran/faster-whisper-base (revision ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66) and https://github.com/openai/whisper |
| PaddleOCR PP-OCRv5 and v6 models | Apache-2.0 | https://github.com/PaddlePaddle/PaddleOCR |
| OpenAI Codex CLI (commit 097558c1bdfc6318cb0ce80a64816ca4d61c7814): base instructions, apply_patch format description and grammar, update_plan tool definition, and the apply_patch parser and matching rules re-implemented in Python for MeshPool's "codex" engine style (`app/defaults/engines/codex`, notice and licence in that folder), Copyright 2025 OpenAI; modified by MeshPool (see the NOTICE comment in `prompt.md`) | Apache-2.0 | https://github.com/openai/codex |
| OpenAI Codex CLI 0.171.0 (openai/codex), Copyright 2025 OpenAI: its base instructions and its `exec_command` tool definition and result format, as captured from one of its requests (`docs/results/codex171-request`), used by MeshPool's opt-in "c171" harness profile (`app/defaults/engines/codex/prompt-171.md`, `app/meshpool_harness/core/tools.py`); modified by MeshPool (see the NOTICE comment in `prompt-171.md`) | Apache-2.0 | https://github.com/openai/codex |
| OpenAI Codex CLI (openai/codex), Copyright 2025 OpenAI: the design of its macOS Seatbelt sandbox profile (allow default, deny file-write*, then allow listed folders, deny network*) and of its `shell_environment_policy` (drop variables named like KEY, SECRET, TOKEN unless allowed) was followed in `app/meshpool_harness/core/sandbox.py` and `config.py` (`scrub_env`); the code is written anew in Python, no Codex source is copied | Apache-2.0 | https://github.com/openai/codex |

## Bundled content (icons and skills)

| Component | License | Source |
|---|---|---|
| Lucide icons 0.460.0 (`app/engines/slides/icons`) | ISC | https://lucide.dev (npm lucide-static) |
| Tabler Icons 3.51.0, outline set without brand logos (`app/engines/icons/tabler`) | MIT | https://tabler.io/icons (npm @tabler/icons) |
| Tabler Icons 3.51.0, filled set without brand logos (`app/engines/icons/bundles/tabler-filled.json.gz`, licence `app/engines/icons/tabler/LICENSE-tabler.txt`) | MIT | https://tabler.io/icons (npm @tabler/icons) |
| Phosphor Icons 2.1.1, six weights without the Brands category (`app/engines/icons/bundles/phosphor.json.gz`, `LICENSE-phosphor.txt`), Copyright (c) 2023 Phosphor Icons | MIT | https://phosphoricons.com (npm @phosphor-icons/core) |
| Iconoir 7.12.2, regular and solid without brand logos (`app/engines/icons/bundles/iconoir.json.gz`, `LICENSE-iconoir.txt`), Copyright (c) 2021 Luca Burgio | MIT | https://iconoir.com (npm iconoir) |
| Heroicons 2.2.0, 24 px outline and solid (`app/engines/icons/bundles/heroicons.json.gz`, `LICENSE-heroicons.txt`), Copyright (c) Tailwind Labs, Inc. | MIT | https://heroicons.com (npm heroicons) |
| Bootstrap Icons 1.13.2 without brand logos (`app/engines/icons/bundles/bootstrap.json.gz`, `LICENSE-bootstrap-icons.txt`), Copyright (c) 2019-2024 The Bootstrap Authors | MIT | https://icons.getbootstrap.com (npm bootstrap-icons) |
| Material Symbols 0.49.0, outlined, rounded and sharp at weight 400, with fill, without product logos (`app/engines/icons/bundles/material.json.gz`, `LICENSE-material-symbols.txt`), by Google | Apache-2.0 | https://fonts.google.com/icons (npm @material-symbols/svg-400) |
| IBM Carbon icons 11.92.0, 32 px, without the Brand category and IBM product marks (`app/engines/icons/bundles/carbon.json.gz`, `LICENSE-carbon.txt`), Copyright IBM Corp. | Apache-2.0 | https://carbondesignsystem.com (npm @carbon/icons) |
| Selawik font, Regular and Bold, compiled unmodified from source (`app/engines/forge/fonts`): measures slides set in Segoe UI | SIL Open Font License 1.1 (`Selawik-LICENSE.txt` beside it) | https://github.com/microsoft/Selawik |
| Mermaid 11.17.2 (`app/meshpool_harness/ui/vendor/mermaid.min.js`, `LICENSE-mermaid.txt`): diagrams in chat | MIT | https://mermaid.js.org |
| KaTeX 0.16.11 (`app/meshpool_harness/ui/vendor/katex.min.js`, CSS and fonts, `LICENSE-katex.txt`): maths in chat | MIT | https://katex.org |
| xterm.js 6.0.0 and its fit addon 0.11.0 (`app/meshpool_harness/ui/vendor/xterm.min.js`, `xterm.css`, `xterm-addon-fit.min.js`, `LICENSE-xterm.txt`, `LICENSE-xterm-addon-fit.txt`; npm @xterm/xterm and @xterm/addon-fit, unmodified but for the source map line): the Terminal panel | MIT | https://xtermjs.org |
| 7-Zip 25.01 (`7zr.exe`, inside the Windows installer: unpacks the program files appended to it) | LGPL-2.1-or-later (the unRAR code is not used) | https://www.7-zip.org |
| Taste Skill, method for building and reviewing user interfaces (`app/defaults/skills/build-ui`, `ui-review`; adapted, not copied), Copyright (c) 2026 Leonxlnx | MIT | https://github.com/Leonxlnx/taste-skill |
| Anthropic knowledge-work plugins: finance, legal, sales and customer-support skills (`app/defaults/skills`) | Apache-2.0 | https://github.com/anthropics/knowledge-work-plugins |
| Hermes Agent (Nous Research), Copyright (c) 2025 Nous Research: the skill folder rules of `tools/skill_manager_tool.py` (name pattern, size limits) and a subset of the scan rules and trust policy of `tools/skills_guard.py`, ported to `app/meshpool_harness/tools_store.py` (rules adapted, no Hermes code imported) | MIT | https://github.com/NousResearch/hermes-agent |

## Designs followed (no code copied)

| Source | License | Used for |
|---|---|---|
| Model Context Protocol specification, `notifications/cancelled` and initialize timeouts (modelcontextprotocol.io) | MIT | Cancelling an MCP call on Stop or timeout (`app/meshpool_harness/agent/mcp.py`) |
| Visual Studio Code, shell environment resolution (`src/vs/platform/shell/node/shellEnv.ts`, Microsoft) | MIT | Reading the login shell's environment once for MCP servers, with a recursion guard (`login_shell_env` in `agent/mcp.py`); re-implemented, not copied |

## Python packages

What the desktop builds install into the bundled Python, dependencies included (`packaging/python-packages.txt`
names them and how they were worked out; `app/tests/test_fix_pass2_open_engines.py` holds this table to it).

| Package | Version | License |
|---|---|---|
| faster-whisper | 1.2.1 | MIT |
| ctranslate2 | 4.9.1 | MIT |
| av (PyAV, with FFmpeg libraries inside its wheel) | 14.1.0 | BSD-3-Clause, with LGPL-2.1+ FFmpeg libraries |
| onnxruntime | 1.19.2 | MIT |
| nbformat, nbclient, nbconvert, ipykernel | 5.11.1, 0.11.0, 7.17.2, 7.4.0 | BSD-3-Clause |
| debugpy | 1.8.5 | MIT |
| pillow-heif (with libheif and libde265 inside its wheel) | 1.8.1 | BSD-3-Clause, with LGPL-3.0 libheif and libde265 |
| matplotlib, contourpy, kiwisolver, fonttools | 3.11.2, 1.4.0, 1.5.1, 4.68.0 | PSF-based (matplotlib), BSD-3-Clause, BSD-3-Clause, MIT |
| agent-client-protocol | 0.9.0 | see package |
| annotated-doc | 0.0.6 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| attrs | 26.1.0 | MIT |
| bottle | 0.13.4 | MIT License |
| cairocffi | 1.7.1 | BSD License |
| CairoSVG | 2.9.1 | LGPL-3.0-or-later |
| certifi | 2026.5.20 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.1 | BSD-3-Clause |
| clr_loader | 0.3.1 | see package |
| colorama | 0.4.6 | BSD License |
| concurrent-log-handler | 0.9.29 | Apache-2.0 |
| croniter | 6.0.0 | MIT License |
| cryptography | 50.0.0 | Apache-2.0 OR BSD-3-Clause |
| cssselect2 | 0.10.1 | BSD License |
| defusedxml | 0.7.1 | Python Software Foundation License |
| distro | 1.9.0 | Apache Software License |
| et_xmlfile | 2.0.0 | MIT License |
| fastapi | 0.162.4 | MIT |
| fire | 0.7.1 | Apache-2.0 |
| firecrawl-anydoc | 0.2.4 | MIT |
| flatbuffers | 25.12.19 | Apache Software License |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| h11 | 0.16.0 | MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.7.0 | BSD-3-Clause |
| httptools | 0.9.0 | MIT |
| httpx | 0.28.1 | BSD License |
| httpx2 | 2.7.0 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| iniconfig (a pytest dependency) | 2.3.1 | MIT |
| Jinja2 | 3.1.6 | BSD License |
| jiter | 0.19.0 | MIT |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| lxml | 6.1.3 | BSD-3-Clause |
| Markdown | 3.10.2 | BSD-3-Clause |
| markdown-it-py | 4.3.0 | MIT License |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| mcp | 2.0.0 | MIT License |
| mcp-types | 2.0.0 | MIT License |
| mdurl | 0.1.2 | MIT License |
| nemo-relay (a Hermes dependency on Linux, Windows and Apple silicon Macs) | 0.8.4 | Apache-2.0 |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| olefile | 0.47 | BSD License |
| openai | 2.24.0 | Apache Software License |
| openpyxl | 3.1.5 | MIT License |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| packaging | 26.0 | Apache-2.0 OR BSD-2-Clause |
| pandas | 3.0.5 | BSD License |
| pathspec | 1.1.1 | Mozilla Public License 2.0 (MPL 2.0) |
| pdfminer.six | 20260107 | MIT |
| pillow | 12.3.0 | MIT-CMU |
| pillow_heif | 1.8.1 | BSD-3-Clause |
| pip (comes with the bundled Python) | 24.1.2 | MIT License |
| playwright | 1.65.1 | Apache-2.0 |
| pluggy (a pytest dependency) | 1.7.0 | MIT |
| portalocker | 4.4.2 | BSD-3-Clause |
| prompt_toolkit | 3.0.52 | BSD License |
| protobuf | 7.36.2 (Mac: 6.33.5) | 3-Clause BSD License |
| proxy_tools | 0.1.0 | MIT License |
| psutil | 7.2.2 | BSD-3-Clause |
| ptyprocess | 0.7.0 | ISC License (ISCL) |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| pydantic_core | 2.46.4 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| PyJWT | 2.13.0 | MIT |
| pyobjc-core | 13.0 | MIT |
| pyobjc-framework-Cocoa | 13.0 | MIT |
| pyobjc-framework-Quartz | 13.0 | MIT |
| pyobjc-framework-Security | 13.0 | MIT |
| pyobjc-framework-UniformTypeIdentifiers | 13.0 | MIT |
| pyobjc-framework-WebKit | 13.0 | MIT |
| pypdf | 7.0.1 | BSD-3-Clause |
| pypdfium2 | 5.15.2 (Mac: 4.30.1) | BSD-3-Clause, Apache-2.0, dependency licenses |
| pytest (runs a project's tests in Code chats with nothing to install) | 9.2.0 | MIT |
| python-dateutil | 4.0.0 | BSD-3-Clause |
| python-docx | 1.2.0 | MIT License |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| python-multipart | 0.0.33 | Apache-2.0 |
| python-pptx | 1.0.2 | MIT License |
| pythonnet | 3.2.1 | MIT |
| pytz | 2026.6 | MIT License |
| pywebview | 6.2.1 | BSD License |
| pywin32 | 311 | Python Software Foundation License |
| pywinpty | 2.0.15 | MIT |
| PyYAML | 6.0.3 | MIT License |
| referencing | 0.37.0 | MIT |
| reportlab | 5.1.0 | BSD License |
| requests | 2.33.0 | Apache Software License |
| rich | 14.3.3 | MIT License |
| rpds-py | 2026.9.1 | MIT |
| ruamel.yaml | 0.18.17 | MIT License |
| ruamel.yaml.clib | 0.2.15 | MIT License |
| sniffio | 1.3.1 | MIT License; Apache Software License |
| snowballstemmer | 3.1.1 | BSD-3-Clause |
| socksio | 1.0.0 | MIT License |
| sse-starlette | 3.7.0 | BSD-3-Clause |
| starlette | 1.3.1 | BSD-3-Clause |
| tenacity | 9.1.4 | Apache Software License |
| termcolor | 3.3.0 | MIT |
| tinycss2 | 1.5.1 | BSD License |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| truststore | 0.10.5 | MIT |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2025.3 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| uvloop | 0.23.0 | Apache Software License; MIT License |
| watchfiles | 1.3.0 | MIT License |
| wcwidth | 0.9.4 | MIT License |
| webencodings | 0.6.1 | BSD License |
| websockets | 15.0.1 | BSD License |
| xlrd | 2.0.2 | BSD License |
| xlsxwriter | 3.3.1 | BSD-2-Clause |
| anthropic | 0.87.0 | MIT |
| appnope | 1.0.0 | BSD-2-Clause |
| coloredlogs | 15.0.1 | MIT License |
| docstring_parser | 0.18.0 | MIT License |
| humanfriendly | 10.0 | MIT License |
| mpmath | 1.3.0 | BSD License |
| pyreadline3 | 3.5.6 | BSD License |
| sympy | 1.14.0 | BSD License |
| asttokens | 3.0.2 | Apache 2.0 |
| beautifulsoup4 | 4.15.0 | MIT License |
| bleach | 6.4.0 | Apache Software License |
| comm | 0.2.3 | BSD License |
| cycler | 0.12.1 | BSD License |
| ddgs | 9.16.1 | MIT |
| duckdb | 2.0.1 | MIT License |
| executing | 2.3.0 | MIT |
| fastjsonschema | 2.22.3 | BSD-3-Clause |
| filelock | 4.3.0 | MIT |
| fsspec | 2026.9.0 | BSD-3-Clause |
| hf-xet | 1.8.0 | Apache-2.0 |
| huggingface_hub | 2.4.0 | Apache-2.0 |
| ipython | 9.19.1 | BSD-3-Clause |
| ipython_pygments_lexers | 1.1.1 | BSD License |
| jedi | 0.20.1 | MIT |
| jupyter_client | 8.10.0 | BSD License |
| jupyter_core | 5.9.1 | BSD-3-Clause |
| jupyterlab_pygments | 0.3.0 | BSD License |
| matplotlib-inline | 0.2.2 | BSD-3-Clause |
| mistune | 3.4.0 | BSD-3-Clause |
| nest-asyncio2 | 1.7.4 | BSD |
| pandocfilters | 1.5.1 | BSD-3-Clause |
| parso | 0.8.7 | MIT |
| pexpect | 4.9.0 | ISC license |
| platformdirs | 4.14.8 | MIT |
| primp | 2.0.1 | MIT License |
| pure_eval | 0.2.4 | MIT |
| pyparsing | 3.3.3 | MIT |
| pyzmq | 27.3.0 | BSD-3-Clause |
| ruff | 0.17.6 | MIT |
| soupsieve | 4.1.3 | MIT |
| stack-data | 0.6.3 | MIT |
| tokenizers | 0.23.3 | Apache Software License |
| tornado | 6.6.1 | Apache-2.0 |
| traitlets | 5.16.1 | BSD License |

## Node packages (Pi)

| Package | Version | License |
|---|---|---|
| @anthropic-ai/sdk | 0.129.0 | MIT |
| @aws-sdk/client-bedrock-runtime | 3.1127.0 | Apache-2.0 |
| @aws-sdk/core | 3.981.0 | Apache-2.0 |
| @aws-sdk/credential-provider-env | 3.972.84 | Apache-2.0 |
| @aws-sdk/credential-provider-http | 3.972.86 | Apache-2.0 |
| @aws-sdk/credential-provider-ini | 3.973.30 | Apache-2.0 |
| @aws-sdk/credential-provider-login | 3.972.92 | Apache-2.0 |
| @aws-sdk/credential-provider-node | 3.972.97 | Apache-2.0 |
| @aws-sdk/credential-provider-process | 3.972.84 | Apache-2.0 |
| @aws-sdk/credential-provider-sso | 3.973.28 | Apache-2.0 |
| @aws-sdk/credential-provider-web-identity | 3.972.90 | Apache-2.0 |
| @aws-sdk/eventstream-handler-node | 3.972.42 | Apache-2.0 |
| @aws-sdk/middleware-eventstream | 3.972.38 | Apache-2.0 |
| @aws-sdk/middleware-websocket | 3.972.66 | Apache-2.0 |
| @aws-sdk/nested-clients | 3.998.3 | Apache-2.0 |
| @aws-sdk/signature-v4-multi-region | 3.996.52 | Apache-2.0 |
| @aws-sdk/token-providers | 3.1166.0 | Apache-2.0 |
| @aws-sdk/types | 3.975.1 | Apache-2.0 |
| @aws-sdk/xml-builder | 3.972.43 | Apache-2.0 |
| @aws/lambda-invoke-store | 0.3.0 | Apache-2.0 |
| @babel/runtime | 7.29.14 | MIT |
| @earendil-works/pi-agent-core | 1.5.2 | MIT |
| @earendil-works/pi-ai | 1.5.2 | MIT |
| @earendil-works/pi-codemode | 1.5.2 | MIT |
| @earendil-works/pi-coding-agent | 1.5.2 | MIT |
| @earendil-works/pi-mcp | 1.5.2 | MIT |
| @earendil-works/pi-schema | 1.5.2 | MIT |
| @earendil-works/pi-telemetry | 1.5.2 | MIT |
| @earendil-works/pi-tui | 1.5.2 | MIT |
| @google/genai | 2.21.0 | Apache-2.0 |
| @isaacs/nonrecursive-glob-match | 1.1.0 | BlueOak-1.0.0 |
| @protobufjs/aspromise | 1.1.2 | BSD-3-Clause |
| @protobufjs/base64 | 1.1.2 | BSD-3-Clause |
| @protobufjs/codegen | 2.0.5 | BSD-3-Clause |
| @protobufjs/eventemitter | 1.1.1 | BSD-3-Clause |
| @protobufjs/fetch | 1.1.1 | BSD-3-Clause |
| @protobufjs/float | 1.0.2 | BSD-3-Clause |
| @protobufjs/path | 1.1.2 | BSD-3-Clause |
| @protobufjs/pool | 1.1.0 | BSD-3-Clause |
| @protobufjs/utf8 | 1.1.2 | BSD-3-Clause |
| @silvia-odwyer/photon-node | 0.3.4 | Apache-2.0 |
| @smithy/core | 3.40.0 | Apache-2.0 |
| @smithy/credential-provider-imds | 4.5.2 | Apache-2.0 |
| @smithy/fetch-http-handler | 5.8.1 | Apache-2.0 |
| @smithy/node-http-handler | 4.12.1 | Apache-2.0 |
| @smithy/signature-v4 | 5.7.5 | Apache-2.0 |
| @smithy/types | 4.20.0 | Apache-2.0 |
| @stablelib/base64 | 1.0.1 | MIT |
| @types/node | 26.6.6 | MIT |
| @types/retry | 0.12.0 | MIT |
| agent-base | 7.1.4 | MIT |
| balanced-match | 4.0.4 | MIT |
| base64-js | 1.5.1 | MIT |
| bignumber.js | 9.3.1 | MIT |
| bowser | 2.15.0 | MIT |
| brace-expansion | 5.0.12 | MIT |
| buffer-equal-constant-time | 1.0.1 | BSD-3-Clause |
| chalk | 6.0.0 | MIT |
| cross-spawn | 7.0.6 | MIT |
| data-uri-to-buffer | 4.0.1 | MIT |
| debug | 4.4.3 | MIT |
| diff | 8.0.4 | BSD-3-Clause |
| ecdsa-sig-formatter | 1.0.11 | Apache-2.0 |
| extend | 3.0.2 | MIT |
| fast-sha256 | 1.3.0 | Unlicense |
| fetch-blob | 3.2.0 | MIT |
| formdata-polyfill | 4.0.10 | MIT |
| gaxios | 7.3.1 | Apache-2.0 |
| gcp-metadata | 8.1.2 | Apache-2.0 |
| get-east-asian-width | 1.6.0 | MIT |
| google-auth-library | 10.9.1 | Apache-2.0 |
| google-logging-utils | 1.1.3 | Apache-2.0 |
| graceful-fs | 4.2.11 | ISC |
| highlight.js | 10.7.3 | BSD-3-Clause |
| hosted-git-info | 9.0.3 | ISC |
| http-proxy-agent | 9.1.0 | MIT |
| https-proxy-agent | 7.0.6 | MIT |
| ignore | 7.0.8 | MIT |
| isexe | 2.0.0 | ISC |
| jiti | 2.7.0 | MIT |
| json-bigint | 1.0.0 | MIT |
| json-schema-to-ts | 3.1.1 | MIT |
| jwa | 2.0.1 | MIT |
| jws | 4.0.1 | MIT |
| long | 5.3.2 | Apache-2.0 |
| lovely-mermaid | 0.3.3 | Apache-2.0 |
| lru-cache | 11.5.3 | BlueOak-1.0.0 |
| marked | 18.0.11 | MIT |
| marked-cjk-friendly | 0.1.2 | MIT |
| minimatch | 11.0.3 | BlueOak-1.0.0 |
| ms | 2.1.3 | MIT |
| node-domexception | 1.0.0 | MIT |
| node-fetch | 3.3.2 | MIT |
| openai | 7.19.0 | Apache-2.0 |
| p-retry | 4.6.2 | MIT |
| partial-json | 0.1.7 | MIT |
| path-key | 3.1.1 | MIT |
| proper-lockfile | 4.1.2 | MIT |
| protobufjs | 7.6.6 | BSD-3-Clause |
| proxy-agent-negotiate | 1.1.0 | MIT |
| quickjs-wasi | 3.6.2 | MIT |
| retry | 0.12.0 | MIT |
| safe-buffer | 5.2.1 | MIT |
| semver | 7.8.5 | ISC |
| shebang-command | 2.0.0 | MIT |
| shebang-regex | 3.0.0 | MIT |
| signal-exit | 3.0.7 | ISC |
| standardwebhooks | 1.1.1 | MIT |
| ts-algebra | 2.0.0 | MIT |
| tslib | 2.9.1 | 0BSD |
| typebox | 1.3.27 | MIT |
| undici | 8.12.3 | MIT |
| undici-types | 8.9.0 | MIT |
| web-streams-polyfill | 3.3.3 | MIT |
| which | 2.0.2 | ISC |
| ws | 8.22.2 | MIT |
| yaml | 2.9.0 | ISC |
