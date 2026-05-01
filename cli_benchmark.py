"""
Huntova `benchmark` command — synthetic hunt against canned fixtures
to measure provider quality without burning real provider quota.
Pattern adapted from OpenClaw's `openclaw bench`; independent Python.
Subcommands: `run [--provider P]` (5-prospect synthetic hunt, 3 repeats;
records score-mean, score-stability, latency p50/p90, cost-est), `compare`
(table of past runs), `fixtures` (list / preview fixture pages). All
support `--json`. Persisted at ~/.local/share/huntova/benchmarks.json.
Wired in cli.py via `register(sub)`.
"""
from __future__ import annotations

import argparse
import json as _json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from tui import bold, dim, red, cyan, green
except Exception:  # pragma: no cover — tui import must not break boot
    bold = dim = red = cyan = green = (lambda s: s)


# ── fixtures (3 archetypes; 5-prospect hunt cycles them) ───────────
_FIXTURES: list[dict[str, str]] = [
    {"id": "fx-agency", "archetype": "high-fit B2B agency",
     "url": "https://acme-marketing.example.com/services",
     "title": "Acme Marketing — B2B SaaS Lead Generation Agency",
     "expected_band": "high", "html":
        "<html><body><h1>Acme Marketing</h1><p>12-person B2B marketing "
        "agency working with mid-market SaaS. Hiring head of growth, "
        "evaluating new outbound prospecting tools — our outdated CRM "
        "costs us 6+ hours/week on manual list building. Series A closed "
        "March 2026, $8M raised. Need to scale outbound by Q3.</p>"
        "<p>Contact: Sarah Chen, VP Ops — sarah@acme-marketing.example.com"
        " — /in/sarah-chen-acme</p></body></html>"},
    {"id": "fx-consumer", "archetype": "wrong-fit B2C consumer",
     "url": "https://emmas-cupcakes.example.com",
     "title": "Emma's Cupcakes — Bakery in Brooklyn",
     "expected_band": "low", "html":
        "<html><body><h1>Emma's Cupcakes</h1><p>Family-owned bakery "
        "serving Brooklyn since 1998. Order red velvet for birthdays and "
        "weddings. Walk-ins Tuesday-Sunday. Cash and card.</p><p>123 Main "
        "St, Brooklyn NY · (555) 010-2244</p></body></html>"},
    {"id": "fx-freelancer", "archetype": "boundary case freelancer",
     "url": "https://j-rivera-consulting.example.com",
     "title": "J. Rivera — Independent Marketing Consultant",
     "expected_band": "mid", "html":
        "<html><body><h1>J. Rivera Consulting</h1><p>Solo marketing "
        "consultant. 14 years agency-side. Freelancing full-time. Open to "
        "short engagements with early-stage SaaS founders. No team, no "
        "infrastructure. Email j.rivera@j-rivera-consulting.example.com."
        "</p></body></html>"},
]


def _hunt_prospects() -> list[dict[str, str]]:
    return [_FIXTURES[i % len(_FIXTURES)] for i in range(5)]


# ── pricing (USD per 1M tokens, approx 2026-04) ────────────────────
_PRICING: dict[str, tuple[float, float]] = {
    "anthropic": (3.00, 15.00), "gemini": (0.30, 2.50),
    "openai": (0.15, 0.60), "openrouter": (3.00, 15.00),
    "groq": (0.59, 0.79), "deepseek": (0.27, 1.10),
    "together": (0.88, 0.88), "mistral": (2.00, 6.00),
    "perplexity": (0.20, 0.20), "ollama": (0.0, 0.0),
    "lmstudio": (0.0, 0.0), "llamafile": (0.0, 0.0), "custom": (0.0, 0.0),
}


# Per-provider chars-per-token (cl100k ~4.0, Claude ~3.3, Gemini ~3.5)
_CHARS_PER_TOKEN = {"anthropic": 3.3, "gemini": 3.5, "openai": 4.0}


def _approx_tokens(text: str, provider: str = "openai") -> int:
    if provider in ("openai", "openrouter"):
        try:
            import tiktoken  # type: ignore[import-not-found]
            return len(tiktoken.get_encoding("cl100k_base").encode(text))
        except Exception: pass
    return max(1, int(len(text or "") / _CHARS_PER_TOKEN.get(provider, 4.0)))


def _est_cost(provider: str, in_text: str, out_text: str) -> float:
    ip, op = _PRICING.get(provider, (0.0, 0.0))
    return (_approx_tokens(in_text, provider) * ip
            + _approx_tokens(out_text, provider) * op) / 1_000_000.0


# ── scoring prompt (mirrors analyse_lead's 5-dim shape) ────────────
_SYS = ("You are a ruthlessly precise B2B lead analyst. Output ONLY a "
        "single valid JSON object (not an array). No markdown.")

_USR = """Score this page as a potential B2B lead for an outbound sales agency.

PAGE: {url}
Title: {title}
HTML:
{html}

Score these 5 dimensions (integer 0-10 each):
- fit_score — does this org match a B2B service-buying profile?
- buyability_score — would they realistically buy via cold outreach?
- reachability_score — can we reach a decision maker?
- service_opportunity_score — how much value could we add?
- timing_score — evidence of active or imminent need?

Respond with ONLY this JSON object (no markdown, no prose):
{{"fit_score":N,"buyability_score":N,"reachability_score":N,"service_opportunity_score":N,"timing_score":N}}
"""


_SCORE_KEYS = ("fit_score", "buyability_score", "reachability_score",
               "service_opportunity_score", "timing_score")


def _build_prompt(fx: dict[str, str]) -> tuple[list[dict[str, str]], str]:
    user = _USR.format(url=fx["url"], title=fx["title"], html=fx["html"][:4000])
    return ([{"role": "system", "content": _SYS},
             {"role": "user", "content": user}], _SYS + "\n\n" + user)


def _parse_score(raw: str) -> dict[str, int] | None:
    if not raw: return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s[3:]
        if s.lower().startswith("json"): s = s[4:]
        s = s.strip().strip("`").strip()
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a: return None
    try: d = _json.loads(s[a:b + 1])
    except Exception: return None
    # Require ≥3 of the 5 score keys; stray `{...}` in chatty replies
    # would otherwise silently parse to all-zeros and skew stability.
    if not isinstance(d, dict): return None
    if sum(1 for k in _SCORE_KEYS if k in d) < 3: return None
    out: dict[str, int] = {}
    for k in _SCORE_KEYS:
        try: out[k] = max(0, min(10, int(float(d.get(k, 0)))))
        except Exception: out[k] = 0
    return out


# ── persistence ────────────────────────────────────────────────────
def _store_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "huntova" / "benchmarks.json"


def _load_runs() -> list[dict[str, Any]]:
    p = _store_path()
    if not p.exists(): return []
    try: return _json.loads(p.read_text() or "[]") or []
    except Exception: return []


def _save_runs(runs: list[dict[str, Any]]) -> None:
    p = _store_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(runs, indent=2, default=str))


# ── benchmark execution ────────────────────────────────────────────
def _pct(values: list[float], p: float) -> float:
    if not values: return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return round(s[k], 3)


def _bench_one_provider(slug: str, n_repeats: int = 3) -> dict[str, Any]:
    """Run the 5-prospect synthetic hunt against a single provider."""
    from providers import get_provider
    try: prov = get_provider({"preferred_provider": slug})
    except Exception as e: return {"provider": slug, "error": str(e), "ok": False}
    prospects = _hunt_prospects()
    latencies: list[float] = []
    json_valid = json_total = 0
    fits_per: list[list[int]] = [[] for _ in prospects]
    cost_total = 0.0
    for _rep in range(n_repeats):
        for idx, fx in enumerate(prospects):
            msgs, ptext = _build_prompt(fx)
            t0 = time.perf_counter()
            try: raw = prov.chat(messages=msgs, max_tokens=512,
                                 temperature=0.35, timeout_s=45.0)
            except Exception as e:
                raw = ""
                if os.environ.get("HV_BENCH_DEBUG"):
                    print(dim(f"  [debug] {slug}:{fx['id']} err: {e}"))
            latencies.append(time.perf_counter() - t0)
            cost_total += _est_cost(slug, ptext, raw)
            json_total += 1
            parsed = _parse_score(raw) if raw else None
            if parsed is not None:
                json_valid += 1; fits_per[idx].append(parsed["fit_score"])
            else: fits_per[idx].append(-1)
    valid = [s for prospect in fits_per for s in prospect if s >= 0]
    stdevs = [statistics.pstdev([s for s in r if s >= 0])
              for r in fits_per if len([s for s in r if s >= 0]) >= 2]
    return {"provider": slug, "model": getattr(prov, "_default_model", "?"),
            "ok": True, "n_prospects": len(prospects), "n_repeats": n_repeats,
            "json_valid_pct": round(100.0 * json_valid / json_total, 1) if json_total else 0.0,
            "score_mean": round(statistics.mean(valid), 2) if valid else 0.0,
            "score_stability": round(statistics.mean(stdevs), 2) if stdevs else 0.0,
            "latency_p50": _pct(latencies, 0.50),
            "latency_p90": _pct(latencies, 0.90),
            "cost_est_usd": round(cost_total, 5),
            "fits_per_prospect": fits_per}


# ── subcommand handlers ────────────────────────────────────────────
def _cmd_run(args: argparse.Namespace) -> int:
    from providers import list_available_providers, _DEFAULT_ORDER
    # When the user hasn't passed --provider, only benchmark what they've
    # actually configured. The previous code fell back to `_DEFAULT_ORDER`
    # if `list_available_providers()` returned empty, which silently
    # benchmarked against providers the user never set up — produced
    # confusing "auth_failed" rows for keys they don't own.
    targets = ([args.provider.strip().lower()] if args.provider
               else list_available_providers())
    if not targets:
        print("[huntova] no providers configured — run `huntova onboard` first.",
              file=sys.stderr); return 1
    print(bold(f"\nRunning benchmark against {len(targets)} provider(s)…\n"))
    results: list[dict[str, Any]] = []
    for slug in targets:
        print(f"  {cyan('▶')} {slug:<11} ", end="", flush=True)
        r = _bench_one_provider(slug, n_repeats=3); results.append(r)
        if not r.get("ok"): print(red(f"failed: {r.get('error', '?')}"))
        else: print(green(f"score={r['score_mean']}  stab=±{r['score_stability']}"
                          f"  p50={r['latency_p50']}s  p90={r['latency_p90']}s  "
                          f"cost=${r['cost_est_usd']}  json={r['json_valid_pct']}%"))
    record = {"timestamp": datetime.now(timezone.utc).isoformat(),
              "results": results}
    runs = _load_runs(); runs.append(record); _save_runs(runs)
    if args.json: print(_json.dumps(record, indent=2, default=str)); return 0
    ok = [r for r in results if r.get("ok")]
    if len(ok) > 1:
        ok.sort(key=lambda r: (-r["score_mean"], r["latency_p50"], r["cost_est_usd"]))
        print(bold("\nRanking (score desc, then latency, then cost):\n"))
        for i, r in enumerate(ok, 1):
            print(f"  {i}. {bold(r['provider']):<11}  score={r['score_mean']}  "
                  f"stab=±{r['score_stability']}  p50={r['latency_p50']}s  "
                  f"cost=${r['cost_est_usd']}")
    print(dim(f"\n  saved → {_store_path()}\n"))
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    runs = _load_runs()
    if not runs:
        print("[huntova] no benchmark runs yet — try `huntova benchmark run`.")
        return 0
    if args.json: print(_json.dumps(runs, indent=2, default=str)); return 0
    print(bold(f"\nPast benchmark runs ({len(runs)} total):\n"))
    print(f"  {dim('when'):<22} {dim('provider'):<11} {dim('score'):<8} "
          f"{dim('±stab'):<8} {dim('p50'):<8} {dim('p90'):<8} {dim('cost')}")
    for record in runs[-20:]:
        ts = (record.get("timestamp") or "")[:19].replace("T", " ")
        for r in record.get("results", []) or []:
            if not r.get("ok"):
                print(f"  {ts:<22} {r.get('provider', '?'):<11} "
                      f"{red('error'):<8} {dim(r.get('error', '')[:40])}"); continue
            print(f"  {ts:<22} {r['provider']:<11} {r['score_mean']:<8} "
                  f"±{r['score_stability']:<7} {r['latency_p50']:<7}s "
                  f"{r['latency_p90']:<7}s ${r['cost_est_usd']}")
    print("")
    return 0


def _cmd_fixtures(args: argparse.Namespace) -> int:
    if args.json: print(_json.dumps(_FIXTURES, indent=2)); return 0
    print(bold(f"\nFixture pages ({len(_FIXTURES)} archetypes, "
               f"5-prospect hunt cycles them):\n"))
    for fx in _FIXTURES:
        print(f"  {cyan(fx['id']):<14}  {bold(fx['archetype'])}")
        print(f"    {dim('url:')}     {fx['url']}")
        print(f"    {dim('title:')}   {fx['title']}")
        print(f"    {dim('expect:')}  fit_score band = {fx['expected_band']}")
        if args.preview:
            print(f"    {dim('html:')}    "
                  f"{(fx['html'] or '')[:240].replace(chr(10), ' ')}…")
        print("")
    return 0


# ── public dispatcher + argparse wiring ────────────────────────────
def cmd_benchmark(args: argparse.Namespace) -> int:
    """Dispatcher: huntova benchmark {run|compare|fixtures}."""
    sub = (getattr(args, "benchmark_cmd", None) or "run").strip().lower()
    if sub == "run":      return _cmd_run(args)
    if sub == "compare":  return _cmd_compare(args)
    if sub == "fixtures": return _cmd_fixtures(args)
    print(f"[huntova] unknown benchmark subcommand {sub!r} — "
          "try run/compare/fixtures", file=sys.stderr)
    return 1


def register(sub) -> None:
    """Attach `benchmark` subparser to cli.py's argparse tree."""
    p = sub.add_parser("benchmark",
        help="Synthetic-hunt provider quality benchmark (no quota burn)",
        description="Run a synthetic hunt against canned fixtures to measure "
                    "provider quality (Claude vs Gemini vs OpenAI vs Ollama, "
                    "etc.) without burning real provider quota.")
    sp = p.add_subparsers(dest="benchmark_cmd")
    r = sp.add_parser("run", help="Run the synthetic 5-prospect hunt")
    r.add_argument("--provider", default=None, metavar="SLUG",
                   help="Single slug (anthropic, gemini, openai, ollama, "
                        "etc.); omit to iterate all configured")
    r.add_argument("--json", action="store_true", help="Emit JSON")
    c = sp.add_parser("compare", help="Table view of past benchmark runs")
    c.add_argument("--json", action="store_true", help="Emit JSON")
    f = sp.add_parser("fixtures", help="List the canned fixture pages")
    f.add_argument("--preview", action="store_true",
                   help="Show first 240 chars of each fixture's HTML")
    f.add_argument("--json", action="store_true", help="Emit JSON")
    p.set_defaults(func=cmd_benchmark)
