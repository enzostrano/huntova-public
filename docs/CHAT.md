# `huntova chat` — natural-language CLI

`huntova chat` is a terminal REPL on top of Huntova's existing
subcommands. You type what you want in plain English; the AI parses your
intent and dispatches to the same code paths as `huntova hunt` /
`huntova ls`.

It reuses the same `providers.get_provider().chat()` path as the rest
of the CLI — same configured key, same provider routing, no new
transport.

## Quick start

```bash
huntova onboard           # one-time, drops a key into the keychain
huntova chat
```

```
  huntova chat  — natural-language CLI
  provider: gemini   exit: :q or Ctrl-C

> find me 10 video studios in Berlin

  ▸ start_hunt  --countries DE --max-leads 10
  icp: video studios in Berlin
  …
> show me my top German leads
  ▸ list_leads  filter=country:Germany
  …
> :q
```

Exit with `:q`, `exit`, `quit`, or Ctrl-C.

## How it works

Every turn, the prompt + last ~20 messages of history are sent to the
configured AI provider in JSON mode (`response_format={"type":
"json_object"}`). The provider is required to reply with one of three
shapes.

### Action shapes

#### `start_hunt`

```json
{
  "action": "start_hunt",
  "countries": ["DE", "FR"],
  "max_leads": 10,
  "timeout_minutes": 15,
  "icp": "video studios in Berlin"
}
```

| Field            | Type     | Default | Notes                                |
|------------------|----------|---------|--------------------------------------|
| `countries`      | string[] | UK,US,DE,FR,ES,IT | ISO codes or names; `[]` falls back to defaults |
| `max_leads`      | integer  | 10      | Hard-capped at 100                   |
| `timeout_minutes`| integer  | 15      | Currently advisory, hunt runs to completion |
| `icp`            | string   | ""      | Free-text ICP description (logged, not yet wired into the agent prompt) |

Dispatches to `cmd_hunt` in-process — same streaming output, same DB
writes.

#### `list_leads`

```json
{
  "action": "list_leads",
  "filter": "country:Germany"
}
```

`filter` follows `huntova ls --filter` syntax: bare substring
(`"aurora"`) or `field:value` (`country:Germany`, `org_name:studio`,
`contact_email:.de`). Fields scanned: `org_name`, `country`, `city`,
`contact_name`, `contact_email`, `why_fit`, `production_gap`,
`email_subject`, `email_status`.

Dispatches to `cmd_ls` with `--limit 20 --format table`.

#### `answer`

```json
{
  "action": "answer",
  "text": "Run `huntova onboard` to add a Gemini key."
}
```

Used for how-to / definition / status questions where there's no
command to dispatch. The text is printed verbatim.

## Validation

Before dispatch we check:

- Top-level value is a JSON object.
- `action` is one of `start_hunt | list_leads | answer`.
- `countries` is coerced to `list[str]` (non-list → empty).
- `max_leads` is clamped to `[1, 100]`.

If parsing fails (non-JSON or schema mismatch) the bad turn is dropped
from history and the user is prompted to rephrase. The next turn starts
clean — bad outputs don't poison context.

## Constraints

- **No new dependencies.** Uses `argparse`, `json`, `re`, plus the
  existing `providers` and `tui` modules.
- **No new provider dispatch.** `providers.get_provider()` resolves the
  user's preferred provider exactly as `huntova hunt` does.
- **History is in-process only.** Capped at 20 messages; not persisted
  across `huntova chat` invocations.
- **TTY-friendly but pipe-safe.** The REPL works with `stdin` redirected
  for scripting.

## Exit codes

| Code | Meaning                                                  |
|------|----------------------------------------------------------|
| `0`  | Clean exit (`:q`, EOF, or Ctrl-C)                        |
| `1`  | No provider configured / provider init failed            |

When you see exit 1, run `huntova onboard` to wire up an API key.
