"""BRAIN-PROD-7 (a586): regression tests pinning agent-runner
concurrency + AGENT-DNA replay-safety invariants. Audit covered:

1. Lease-TTL boundary semantics for `_dna_state="pending"`.
2. Restart-safety: `_dna_dirty` flag must NOT bleed across runs.
3. Background `_refine()` writes are gated on `agent_running` so a
   coroutine that completes after stop doesn't strand state on a
   quiescent context.
4. Cancellation primitive guarantees (cooperative flag, not thread
   kill — but the flag must propagate consistently).
5. Restart-after-crash: ghost thread entries are cleaned, lease is
   reclaimable, MAX_CONCURRENT_AGENTS=1 is preserved.

Per Huntova engineering review on lease-coherence + replay-safety:
strict-expiry boundary `age > ttl` (not `age >= ttl`) avoids two
contenders both believing they own the boundary instant; every
restart-safety flag must be reset at run-end so background tasks
that complete between runs don't leak into the next.

Validated via Perplexity (2026-05-04 09:08): "treat a lease as
stale only when now - started_at > ttl, not at exactly ttl …
boundaries should avoid ambiguity rather than invite concurrent
re-execution."
"""
from __future__ import annotations
from datetime import datetime, timedelta


def test_lease_ttl_boundary_at_exactly_ttl_is_fresh():
    """Strict expiry: at age == TTL, the lease is still fresh
    (not yet stale). Inclusive comparison would invite two
    contenders — one expiring, one claiming — to both believe
    they own the boundary instant.

    Uses the `now=` injection point on `_dna_pending_is_stale`
    to pin the boundary deterministically (no wall-clock race
    between subtraction and comparison).
    """
    import server as _s
    ttl = _s._DNA_PENDING_STALE_AFTER_SEC
    now = datetime(2026, 1, 1, 12, 0, 0)
    started_at_exact_ttl = (now - timedelta(seconds=ttl)).isoformat()
    assert _s._dna_pending_is_stale(started_at_exact_ttl, now=now) is False, (
        "BRAIN-PROD-7 regression: lease-TTL boundary must be strict "
        "(age > ttl). At exactly ttl, the lease is still inside its "
        "valid window. Inclusive comparison (age >= ttl) creates the "
        "two-contender race Perplexity guidance flags."
    )


def test_lease_ttl_just_past_boundary_is_stale():
    """Strict expiry: at age = ttl + epsilon, lease is stale.
    Confirms the strict boundary releases the lease as soon as
    we cross it.
    """
    import server as _s
    ttl = _s._DNA_PENDING_STALE_AFTER_SEC
    now = datetime(2026, 1, 1, 12, 0, 0)
    started_at_past = (now - timedelta(seconds=ttl + 1)).isoformat()
    assert _s._dna_pending_is_stale(started_at_past, now=now) is True


def test_lease_ttl_well_before_boundary_is_fresh():
    """Sanity: a lease 1 second old is unambiguously fresh."""
    import server as _s
    now = datetime(2026, 1, 1, 12, 0, 0)
    started_at_recent = (now - timedelta(seconds=1)).isoformat()
    assert _s._dna_pending_is_stale(started_at_recent, now=now) is False


def test_lease_ttl_is_strict_gt_in_source():
    """Source-level pin: the comparison MUST be `>` not `>=`.
    This is the load-bearing line — every reader of
    `_dna_state=pending` consults this helper, and a future
    refactor that flips the operator would silently invite the
    boundary-race Perplexity flagged.
    """
    import server as _s
    import inspect
    src = inspect.getsource(_s._dna_pending_is_stale)
    # The function ends with `return age > _DNA_PENDING_STALE_AFTER_SEC`
    # — assert the strict-gt comparison is present and `>=` is NOT.
    assert "age > _DNA_PENDING_STALE_AFTER_SEC" in src, (
        "BRAIN-PROD-7 regression: strict expiry comparison `age > "
        "_DNA_PENDING_STALE_AFTER_SEC` must be present. Per "
        "Huntova engineering review on lease-coherence + Perplexity "
        "guidance on TTL boundary semantics."
    )
    assert "age >= _DNA_PENDING_STALE_AFTER_SEC" not in src, (
        "BRAIN-PROD-7 regression: inclusive comparison `>=` invites "
        "two contenders at the boundary instant. Strict `>` only."
    )


def test_run_thread_finally_clears_dna_dirty():
    """`_dna_dirty` is set by the feedback-refine background
    coroutine to signal the running hunt's batch loop should
    swap in fresh DNA. If a refine completes after the agent
    stops, the flag stays True forever and bleeds into the next
    run. The agent-runner finally block MUST reset it.
    """
    import inspect
    import agent_runner as _ar
    src = inspect.getsource(_ar.AgentRunner._run_agent_thread)
    assert "_dna_dirty = False" in src, (
        "BRAIN-PROD-7 regression: `_run_agent_thread` finally must "
        "reset `ctx._dna_dirty = False`. A leftover dirty flag "
        "causes the next run's first batch boundary to misread the "
        "fresh DNA as 'refined mid-hunt' and emit a misleading log, "
        "and worse, can swap the next run's fresh DNA for a stale "
        "refine result that completed between the two runs."
    )


def test_run_thread_finally_clears_cached_dna():
    """Companion invariant: `_cached_dna` is cleared in finally.
    Already in place pre-a586 — pinned here so a future refactor
    can't drop it without breaking this test.
    """
    import inspect
    import agent_runner as _ar
    src = inspect.getsource(_ar.AgentRunner._run_agent_thread)
    assert "_cached_dna = None" in src, (
        "BRAIN-PROD-7 regression: `_cached_dna` must be cleared in "
        "the agent-thread finally. Otherwise stale DNA from run N "
        "could leak into run N+1 if the new run's DNA load fails."
    )


def test_refine_hot_load_gated_on_agent_running():
    """The feedback-refine background coroutine writes
    `_cached_dna` + `_dna_dirty` directly onto the user's
    context. If the agent has STOPPED between feedback save and
    DNA generation completing, those writes strand on a
    quiescent context — the next run picks them up.

    Per BRAIN-PROD-7: the write must be gated on
    `_ctx.agent_running`. If False, the DB save is sufficient
    (the next run reloads from DB).
    """
    import inspect
    import server as _s
    src = inspect.getsource(_s.api_lead_feedback)
    assert "agent_running" in src, (
        "BRAIN-PROD-7 regression: `_refine()` background coroutine "
        "must gate its hot-load writes on `_ctx.agent_running`. "
        "Otherwise a refine that completes between runs strands DNA "
        "state on the context and bleeds into the next run."
    )


def test_check_stop_consults_thread_local_subagent_event():
    """The cancellation primitive in app.py is cooperative: the
    agent thread polls `_check_stop()` between operations.
    `_check_stop()` MUST consult the thread-local subagent
    cancel_event so a cancelled subagent halts mid-crawl
    without waiting for the 14-page deep-research to finish.

    Pin the wiring so a future refactor can't drop the consult.
    """
    import inspect
    import app as _app
    src = inspect.getsource(_app._check_stop)
    assert "subagent_cancel_event" in src, (
        "BRAIN-PROD-7 regression: `_check_stop()` must consult the "
        "thread-local `subagent_cancel_event` set by agent_runner's "
        "subagent runner. Without this consult, Cancel in the Agent "
        "panel waits for the deep-research crawl to finish before "
        "honoring the cancel — defeating the cancellation primitive."
    )


def test_start_agent_honors_pending_stop_without_spawning_thread():
    """If a user clicked Stop while queued and their slot opens
    up between Stop and the next start_agent attempt, the runner
    MUST honor the stop intent (clear the flag, return
    `status=stopped`) instead of clobbering the ctrl and
    spawning the agent anyway.

    This pins the bug-#64 fix — a regression here re-introduces
    the silent-stop-loss bug.
    """
    import inspect
    import agent_runner as _ar
    src = inspect.getsource(_ar.AgentRunner.start_agent)
    # Look for the "stop wins over start" branch.
    assert (
        '"action") == "stop"' in src or "'action') == 'stop'" in src
    ), (
        "BRAIN-PROD-7 regression: `start_agent` must check whether "
        "the user requested Stop before spawning the thread. "
        "Otherwise a Stop pressed during queue-wait gets clobbered "
        "by the slot-opens-up branch."
    )


def test_process_queue_honors_pending_stop():
    """Mirror of the start_agent invariant: when a queue slot
    opens up via _process_queue, a queued user who pressed Stop
    must be honored (skip the spawn, emit cancel status) rather
    than spawned anyway. Pins bug-#65.
    """
    import inspect
    import agent_runner as _ar
    src = inspect.getsource(_ar.AgentRunner._process_queue)
    assert (
        '"action") == "stop"' in src or "'action') == 'stop'" in src
    ), (
        "BRAIN-PROD-7 regression: `_process_queue` must honor a "
        "pending stop on a queued user before spawning the thread."
    )


def test_max_concurrent_agents_is_one():
    """The single-slot model is load-bearing. Cloud-side billing,
    SearXNG rate limits, and the per-user thread-local agent-ctrl
    pattern all assume one agent at a time. A future bump to >1
    breaks several invariants without warning.
    """
    import config as _c
    assert _c.MAX_CONCURRENT_AGENTS == 1, (
        "BRAIN-PROD-7 regression: MAX_CONCURRENT_AGENTS must "
        "remain 1. Several callers (subagent slot accounting, "
        "SearXNG rate-limit headroom, the per-user thread-local "
        "agent-ctrl) assume single-agent. Bumping requires an "
        "explicit audit of every is_running / running_count site."
    )


def test_is_running_self_cleans_dead_thread():
    """If the agent thread died via OS kill / OOM before its
    finally block ran, `_running[user_id]` holds a Thread whose
    is_alive() returns False. is_running MUST self-clean instead
    of returning True forever — otherwise the user can never
    restart their agent.
    """
    import threading as _th
    import agent_runner as _ar

    runner = _ar.AgentRunner()
    # Make a thread that finishes immediately so is_alive() is
    # False without our test having to manage timing.
    t = _th.Thread(target=lambda: None, daemon=True)
    t.start()
    t.join()  # is_alive() == False after this
    runner._running[999] = t  # plant a ghost entry

    # is_running must self-clean and return False.
    result = runner.is_running(999)
    assert result is False, (
        "BRAIN-PROD-7 regression: is_running must self-clean a "
        "dead thread entry. Otherwise an OS-killed agent leaves "
        "the user permanently unable to restart."
    )
    assert 999 not in runner._running, (
        "BRAIN-PROD-7 regression: ghost entry must be removed from "
        "_running so MAX_CONCURRENT_AGENTS slot is freed."
    )


def test_dna_pending_missing_started_at_treated_as_stale():
    """Fail-open recovery: a `_dna_state=pending` row with no
    `_dna_started_at` (corruption / legacy / partial write) is
    treated as stale. Without this, a corrupted row permanently
    blocks the user behind a dead lease marker.
    """
    import server as _s
    assert _s._dna_pending_is_stale(None) is True
    assert _s._dna_pending_is_stale("") is True
    assert _s._dna_pending_is_stale("not-a-timestamp") is True


def test_lease_ttl_env_override_respected():
    """Operators can tune the lease-TTL via
    HV_DNA_PENDING_STALE_SEC. Pin the env-var contract so a
    future refactor can't silently rename or drop it.
    """
    import server as _s
    # Constant must be parsed from HV_DNA_PENDING_STALE_SEC.
    import inspect
    src = inspect.getsource(_s)
    # Looser source check — the env var name must be present and
    # tied to _DNA_PENDING_STALE_AFTER_SEC.
    assert "HV_DNA_PENDING_STALE_SEC" in src, (
        "BRAIN-PROD-7 regression: HV_DNA_PENDING_STALE_SEC env "
        "override must remain. Operators tuning the TTL change "
        "one place; renaming silently breaks their config."
    )
    # The constant itself is sane.
    assert _s._DNA_PENDING_STALE_AFTER_SEC > 0, (
        "BRAIN-PROD-7 regression: lease-TTL must be positive."
    )
