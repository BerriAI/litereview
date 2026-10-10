# Third-party notices

LiteReview's original application code and documentation are licensed under the [MIT License](LICENSE). Dependencies, the Codex runtime, fonts, and third-party marks retain their own licenses and terms. LiteReview's MIT license does not relicense them.

## Browser application

Every production build generates `frontend/dist/third-party-licenses.txt` from the exact installed production dependencies in `frontend/package-lock.json`. It includes their copyright and license texts, including React (MIT), Lucide (ISC), the Markdown libraries (MIT), and Geist/Geist Mono (SIL Open Font License 1.1). The build fails if a production dependency has no license text. Distribute this file with the browser assets; it is linked from the in-app documentation.

## Python and container dependencies

Python dependencies are resolved in `uv.lock` and installed with their distribution metadata and license files. Preserve those files when redistributing an installation or container. Most application dependencies use MIT, Apache-2.0, BSD, or similar licenses. Psycopg and psycopg-binary use LGPL-3.0; certifi and parts of tqdm use MPL-2.0. These packages retain their own license, notice, source-availability, and redistribution requirements. Consult the installed package's license before modifying or repackaging it.

Container base images, system packages, Chromium, and sandbox tools retain their upstream licenses. Their package managers install their license notices. The application image includes this notice and LiteReview's `LICENSE` in `/app`.

## Agent runtime and services

Every review sandbox installs `@openai/codex`, licensed under Apache-2.0. Its version is pinned in `runner/catalog.py`. Preserve the package's license and notices when redistributing the runtime; LiteReview's MIT license does not relicense Codex.

Modal, GitHub, Google OAuth, model providers, gateways, and connected MCP services have their own service terms, access requirements, and charges. An open-source application license does not provide credentials or credits for those services.

## Branding

The LiteLLM train and favicon are BerriAI project branding. The OpenAI, Anthropic, and GitHub SVGs identify the corresponding integrations; their marks belong to their respective owners. The MIT license grants no trademark rights or endorsement. Preserve upstream notices and follow each owner's brand rules when reusing marks. Forks should use their own product branding to avoid implying an official service.
