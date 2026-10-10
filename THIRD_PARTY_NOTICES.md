# Third-party notices

LiteReview's original application code and documentation are licensed under the [MIT License](LICENSE). Dependencies, optional agent runtimes, fonts, and third-party marks retain their own licenses and terms. LiteReview's MIT license does not relicense them.

## Browser application

Every production build generates `frontend/dist/third-party-licenses.txt` from the exact installed production dependencies in `frontend/package-lock.json`. It includes their copyright and license texts, including React (MIT), Lucide (ISC), the Markdown libraries (MIT), and Geist/Geist Mono (SIL Open Font License 1.1). The build fails if a production dependency has no license text. Distribute this file with the browser assets; it is linked from the in-app documentation.

## Python and container dependencies

Python dependencies are resolved in `uv.lock` and installed with their distribution metadata and license files. Preserve those files when redistributing an installation or container. Most application dependencies use MIT, Apache-2.0, BSD, or similar licenses. Psycopg and psycopg-binary use LGPL-3.0; certifi and parts of tqdm use MPL-2.0. These packages retain their own license, notice, source-availability, and redistribution requirements. Consult the installed package's license before modifying or repackaging it.

Container base images, system packages, Chromium, and sandbox tools retain their upstream licenses. Their package managers install their license notices. The application image includes this notice and LiteReview's `LICENSE` in `/app`.

## Optional agent runtimes and services

The versions and package names are pinned in `runner/catalog.py`. Runtimes are installed from their upstream packages into the user's Modal sandbox only when selected.

| Runtime | Package | License or terms |
| --- | --- | --- |
| Built-in | LiteReview runner | MIT |
| Pi | `@earendil-works/pi-coding-agent` | MIT |
| OpenCode V1 | `opencode-ai` | MIT |
| OpenCode V2 | `@opencode/cli` | MIT |
| Codex | `@openai/codex` | Apache-2.0 |
| Claude Code | `@anthropic-ai/claude-agent-sdk` | Anthropic's separate [legal agreements](https://code.claude.com/docs/en/legal-and-compliance); not licensed by LiteReview under MIT |
| Deep Agents | `deepagents` | MIT |

The pinned Claude Agent SDK package states: “© Anthropic PBC. All rights reserved. Use is subject to the Legal Agreements outlined here: https://code.claude.com/docs/en/legal-and-compliance.” Its README refers to the [Anthropic Commercial Terms](https://www.anthropic.com/legal/commercial-terms). Review those terms when enabling that integration or redistributing its runtime.

Modal, GitHub, Google OAuth, model providers, gateways, and connected MCP services have their own service terms, access requirements, and charges. An open-source application license does not provide credentials or credits for those services.

## Branding

The LiteLLM train and favicon are BerriAI project branding. The OpenAI, Anthropic, and GitHub SVGs identify the corresponding integrations; their marks belong to their respective owners. The MIT license grants no trademark rights or endorsement. Preserve upstream notices and follow each owner's brand rules when reusing marks. Forks should use their own product branding to avoid implying an official service.
