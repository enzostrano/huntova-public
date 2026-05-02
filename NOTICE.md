# Third-Party Notices

Huntova is licensed under **AGPL-3.0-or-later** (see `LICENSE`). It is
an independent Python implementation. No third-party source code is
reproduced verbatim in this repository.

## Runtime dependencies

The Python packages listed in `pyproject.toml` are installed alongside
Huntova when you run `pipx install huntova`. Each carries its own
license — most are MIT, BSD, or Apache-2.0. You can review them with:

```bash
pipx runpip huntova show <package-name>
```

Huntova does not redistribute these dependencies' source code; they are
declared in `pyproject.toml` and fetched by `pip` / `pipx` from PyPI at
install time.

If you redistribute a Huntova binary that bundles dependencies (e.g.
via PyInstaller or a Docker image), you are responsible for preserving
each dependency's own license file in your redistribution.

## Trademarks

Names of third-party tools mentioned anywhere in Huntova documentation,
release notes, or marketing copy (e.g. Apollo, Clay, Hunter, Instantly,
Lemlist, Mailmeteor, GlockApps, OpenAI, Anthropic, Gemini, OpenRouter,
Groq, DeepSeek, Together, Mistral, Perplexity, Ollama, LM Studio,
llamafile, SearXNG, Slack, Discord, Telegram, WhatsApp, Twilio, Stripe,
HubSpot, Pipedrive, Calendly, Google) are trademarks of their respective
owners. References are comparative and factual; Huntova is not
affiliated with, endorsed by, or partnered with any of them.

## Questions

For licensing questions or reuse permissions, please open an issue at
<https://github.com/enzostrano/huntova-public/issues>.
