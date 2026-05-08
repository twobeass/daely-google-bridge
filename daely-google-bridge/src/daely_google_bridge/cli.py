"""CLI entry point.

Subcommands:
    bridge bootstrap                  Interactive setup: Daely login, Google OAuth, sub-calendars.
    bridge run [--once]               Run the sync loop.
    bridge status                     Show what the Store knows about state.
    bridge doctor [--live]            Run a series of health checks; optional live ping.
    bridge resync [--calendar <id>]   Force a re-patch of mappings on the next cycle.
    bridge re-color                   Alias for `resync` (clear, discoverable name).

`resync` and `re-color` are local-only — they reset `last_seen_updated=NULL`
on existing event_mapping rows so the next `bridge run` patches them with
the current mapper output. The actual Google calls happen on the next
sync cycle, not in the resync command itself.

`doctor` runs purely local checks by default (DB schema, tokens, mappings,
last sync age, recent error trend). `--live` adds a Daely refresh + a
Google list-calendars ping — useful before an Update or for diagnosing
"why is sync stuck".
"""
from __future__ import annotations

import argparse
import getpass
import shutil
import signal
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import structlog

from .config import BridgeConfig, load_config, save_config
from .daely_client import DaelyAuthError, DaelyClient
from .google_client import TOKEN_PROVIDER, GoogleClient
from .health_server import BridgeState, start_health_server
from .store import Store
from .sync import SyncReport, full_sync, incremental_sync

DEFAULT_CONFIG_PATH = Path("config.yaml")
DAELY_TOKEN_PROVIDER = "daely"

log = structlog.get_logger(__name__)


# ─────────────────── helpers ───────────────────

def _setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Minimal structlog setup. The full sync-loop module will replace this."""
    import logging
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        stream=sys.stderr,
    )
    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            renderer,
        ],
    )


def _ensure_config_exists(path: Path) -> None:
    """If config.yaml is missing, copy config.example.yaml as a starting point."""
    if path.exists():
        return
    example = Path(__file__).resolve().parent.parent.parent / "config.example.yaml"
    if not example.exists():
        # Fall back: emit a tiny stub the user must edit.
        path.write_text(
            "daely_email: change-me@example.com\n"
            "google_oauth_client_secrets_file: ./secrets/google_oauth_client.json\n"
        )
        return
    shutil.copy(example, path)
    print(f"created {path} from config.example.yaml — please edit before running bootstrap.",
          file=sys.stderr)


def _profile_id_to_name(
    calendars: list,
) -> dict[str, str]:
    """Best-effort mapping from profile UUID → display name.

    The /api/groups/<gid>/calendars response doesn't carry profile names.
    For the bootstrap-MVP we synthesise a name from the calendar's title
    when there's exactly one internal calendar per profile (the typical case).
    Otherwise the profile's name is just its UUID short prefix.
    """
    out: dict[str, str] = {}
    for cal in calendars:
        if cal.calendarType != 0 or cal.profileId is None:
            continue
        if cal.profileId not in out:
            # heuristic name source
            out[cal.profileId] = cal.title or f"Profile {cal.profileId[:8]}"
    return out


# ─────────────────── command implementations ───────────────────

def cmd_bootstrap(
    args: argparse.Namespace,
    *,
    daely_factory: Callable[[], DaelyClient] | None = None,
    google_authorize: Callable[..., object] | None = None,
    google_factory: Callable[..., GoogleClient] | None = None,
    input_fn: Callable[[str], str] = input,
    getpass_fn: Callable[[str], str] = getpass.getpass,
) -> int:
    """Run the interactive bootstrap flow.

    All external collaborators are injectable so test_cli can run the whole
    flow without touching the real Daely or Google services.
    """
    config_path = Path(args.config or DEFAULT_CONFIG_PATH)
    _ensure_config_exists(config_path)
    cfg = load_config(config_path)
    _setup_logging(cfg.log_level, cfg.log_format)

    log.info("bootstrap.start", config_path=str(config_path), daely_email=cfg.daely_email)
    store = Store(cfg.db_path)

    # ─── (a) Daely login ───
    daely = (daely_factory or DaelyClient)()
    daely.min_pause_seconds = cfg.daely_min_pause_seconds
    print("Daely login required.")
    if cfg.daely_email != "you@example.com":
        print(f"  Email (from config): {cfg.daely_email}")
        email = cfg.daely_email
    else:
        email = input_fn("  Email: ").strip()
    password = getpass_fn("  Password (input hidden): ")
    try:
        daely.login_password(email, password)
    except DaelyAuthError as e:
        print(f"\nLogin failed: {e}", file=sys.stderr)
        return 2

    if daely.refresh_token:
        store.put_token(
            provider=DAELY_TOKEN_PROVIDER,
            refresh_token=daely.refresh_token,
            access_token=daely.access_token,
        )
    print("  Daely login OK.\n")

    # ─── (b) Daely group + calendars ───
    groups = daely.get_my_groups()
    if not groups:
        print("No Daely groups found on this account. Aborting.", file=sys.stderr)
        return 3
    if len(groups) > 1:
        print(f"  Note: {len(groups)} groups found; using the first ({groups[0].name}).")
    group = groups[0]
    print(f"Group: {group.name} (id={group.id})")

    calendars = daely.get_calendars(group.id)
    internal = [c for c in calendars if c.calendarType == 0]
    print(f"  {len(calendars)} calendars total, {len(internal)} internal (calendarType=0)")

    profile_names = _profile_id_to_name(calendars)
    profile_ids = sorted(profile_names.keys())
    if not profile_ids:
        # Internal calendars with no profileId still need a fallback target
        print("  No profiled calendars found; bootstrap will still set up a fallback calendar.")
    else:
        print(f"  Profiles discovered: {len(profile_ids)}")
        for pid in profile_ids:
            print(f"    - {profile_names[pid]}  ({pid[:8]}…)")

    # ─── (c) Google OAuth ───
    print("\nGoogle authorization required.")
    print(f"  client_secrets file: {cfg.google_oauth_client_secrets_file}")
    if not cfg.google_oauth_client_secrets_file.exists():
        print(
            f"  ERROR: client secrets file does not exist: "
            f"{cfg.google_oauth_client_secrets_file}",
            file=sys.stderr,
        )
        return 4

    auth_fn = google_authorize or GoogleClient.authorize_via_local_server
    google_creds = auth_fn(
        cfg.google_oauth_client_secrets_file,
        scopes=cfg.google_oauth_scopes,
        port=cfg.oauth_local_port,
    )
    GoogleClient.persist_credentials(google_creds, store)
    print("  Google authorization OK.\n")

    # Build Google client for sub-calendar provisioning
    gc_factory = google_factory or GoogleClient
    google = gc_factory(credentials=google_creds)

    # ─── (d) reconcile sub-calendars ───
    existing_google = {c.get("summary"): c.get("id") for c in google.list_calendars()}
    new_mapping: dict[str, str] = dict(cfg.profile_calendar_mapping)
    created_count = 0
    reused_count = 0
    cal_tz_by_profile = {
        c.profileId: c.timeZone for c in internal if c.profileId
    }
    for pid in profile_ids:
        # Already mapped from earlier bootstrap?
        if pid in new_mapping:
            print(f"  reuse: profile {profile_names[pid]} → {new_mapping[pid]}")
            reused_count += 1
            continue
        summary = f"Daely – {profile_names[pid]}"
        if summary in existing_google:
            new_mapping[pid] = existing_google[summary]
            print(f"  found existing Google calendar '{summary}' → {new_mapping[pid]}")
            reused_count += 1
            continue
        tz = cal_tz_by_profile.get(pid) or "UTC"
        created = google.create_calendar(summary=summary, time_zone=tz)
        new_mapping[pid] = created["id"]
        print(f"  created Google calendar '{summary}' → {created['id']}")
        created_count += 1

    # Fallback calendar for non-profiled internal calendars
    fallback_summary = "Daely – Family"
    if not cfg.fallback_google_calendar_id:
        existing_id = existing_google.get(fallback_summary)
        if existing_id:
            cfg = cfg.model_copy(update={"fallback_google_calendar_id": existing_id})
            print(f"  found existing Google fallback '{fallback_summary}' → {existing_id}")
            reused_count += 1
        else:
            tz = next(
                (c.timeZone for c in internal if c.profileId is None and c.timeZone),
                "UTC",
            )
            created = google.create_calendar(summary=fallback_summary, time_zone=tz)
            cfg = cfg.model_copy(update={"fallback_google_calendar_id": created["id"]})
            print(f"  created Google fallback '{fallback_summary}' → {created['id']}")
            created_count += 1

    # ─── (e) write config back ───
    cfg = cfg.model_copy(update={"profile_calendar_mapping": new_mapping})
    save_config(cfg, config_path, backup=True)
    print(f"\nUpdated {config_path} (backup at {config_path}.bak).")

    # ─── (f) summary ───
    print()
    print(f"{len(profile_ids)} profile(s) discovered, "
          f"{created_count} sub-calendar(s) created, "
          f"{reused_count} reused.")
    print("Bootstrap done. Next: `bridge run` (not implemented yet — Phase 3d).")
    daely.close()
    store.close()
    return 0


def _build_daely_client(store: Store, cfg: BridgeConfig) -> DaelyClient:
    """Construct DaelyClient from a stored refresh token."""
    record = store.get_token("daely")
    if record is None:
        raise RuntimeError(
            "no Daely refresh-token in store. Run `bridge bootstrap` first."
        )
    daely = DaelyClient(min_pause_seconds=cfg.daely_min_pause_seconds)
    daely.set_tokens(access_token=record.access_token, refresh_token=record.refresh_token)
    # Refresh proactively so the first request doesn't pay the 401-roundtrip.
    try:
        token_response = daely.refresh()
        store.put_token(
            provider="daely",
            refresh_token=daely.refresh_token,
            access_token=daely.access_token,
        )
        log.debug("run.daely_refreshed", expires_in=token_response.get("expires_in"))
    except DaelyAuthError:
        log.exception("run.daely_refresh_failed")
        raise
    return daely


def _build_google_client(store: Store, cfg: BridgeConfig) -> GoogleClient:
    creds = GoogleClient.load_credentials(
        store, cfg.google_oauth_client_secrets_file, scopes=cfg.google_oauth_scopes,
    )
    if creds is None:
        raise RuntimeError(
            "no Google credentials in store. Run `bridge bootstrap` first."
        )
    return GoogleClient(credentials=creds)


def _print_report(report: SyncReport) -> None:
    print(f"sync run_id={report.run_id}")
    print(f"  inserts:        {report.inserts}")
    print(f"  patches:        {report.patches}")
    print(f"  deletes:        {report.deletes}")
    print(f"  no-ops:         {report.no_ops}")
    print(f"  skip-external:  {report.skipped_external_calendar_events}")
    print(f"  skip-no-target: {report.skipped_no_target_events}")
    print(f"  errors:         {len(report.errors)}")
    print(f"  duration:       {report.duration_seconds:.2f}s")
    if report.errors:
        print("  --- errors ---")
        for daely_id, msg in report.errors[:10]:
            print(f"    {daely_id}: {msg}")
        if len(report.errors) > 10:
            print(f"    … and {len(report.errors) - 10} more")


def cmd_run(
    args: argparse.Namespace,
    *,
    daely_factory: Callable[[Store, BridgeConfig], DaelyClient] | None = None,
    google_factory: Callable[[Store, BridgeConfig], GoogleClient] | None = None,
    full_sync_fn: Callable | None = None,
    incremental_sync_fn: Callable | None = None,
    scheduler_factory: Callable | None = None,
) -> int:
    """Run the sync loop.

    `--once`: a single full_sync, then exit.
    Otherwise: full_sync once, then APScheduler runs incremental_sync every
    config.poll_interval_minutes minutes until SIGTERM/SIGINT.

    Collaborators are injectable so test_cli can drive the dispatch logic
    without booting any real Daely or Google connection.
    """
    config_path = Path(args.config or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        print(f"No config at {config_path}. Run `bridge bootstrap` first.", file=sys.stderr)
        return 1
    cfg = load_config(config_path)
    _setup_logging(cfg.log_level, cfg.log_format)
    store = Store(cfg.db_path)

    daely_b = daely_factory or _build_daely_client
    google_b = google_factory or _build_google_client
    fsync = full_sync_fn or full_sync
    isync = incremental_sync_fn or incremental_sync

    try:
        daely = daely_b(store, cfg)
        google = google_b(store, cfg)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        store.close()
        return 1

    # Shared state for the optional health server. Started below in daemon
    # mode only — `--once` runs don't need an HTTP server.
    state = BridgeState(poll_interval_minutes=cfg.poll_interval_minutes)

    log.info("run.start", once=bool(args.once))
    report = fsync(daely, google, store, cfg)
    state.update_from_report(report)
    _print_report(report)

    if args.once:
        store.close()
        try:
            daely.close()
        except Exception:
            pass
        return 0

    # Optional health server, started before the scheduler so /readyz works
    # before the first incremental cycle completes.
    health_server = None
    if cfg.health_server.enabled:
        try:
            health_server, _ = start_health_server(
                state, store,
                host=cfg.health_server.bind_host,
                port=cfg.health_server.bind_port,
            )
            log.info(
                "run.health_server_started",
                host=cfg.health_server.bind_host,
                port=cfg.health_server.bind_port,
            )
            print(f"Health endpoints on http://{cfg.health_server.bind_host}:"
                  f"{cfg.health_server.bind_port}/{{healthz,readyz,status}}")
        except OSError as e:
            log.warning("run.health_server_start_failed", err=repr(e))
            print(f"WARNING: health server failed to start: {e}", file=sys.stderr)

    # Background scheduler with graceful shutdown.
    if scheduler_factory is None:
        from apscheduler.schedulers.blocking import BlockingScheduler
        scheduler = BlockingScheduler(timezone="UTC")
    else:
        scheduler = scheduler_factory()

    def _job() -> None:
        try:
            r = isync(daely, google, store, cfg)
            state.update_from_report(r)
            _print_report(r)
        except Exception:
            log.exception("run.incremental_failed")

    scheduler.add_job(
        _job, "interval", minutes=cfg.poll_interval_minutes, id="incremental",
    )
    log.info("run.scheduler_started", interval_min=cfg.poll_interval_minutes)
    print(f"\nIncremental sync every {cfg.poll_interval_minutes}m. Ctrl+C to stop.")

    def _shutdown(signum, _frame):
        log.info("run.shutdown_requested", signal=signum)
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        if health_server is not None:
            try:
                health_server.shutdown()
            except Exception:
                pass
        store.close()
        try:
            daely.close()
        except Exception:
            pass
    log.info("run.stopped")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print whatever the Store knows."""
    config_path = Path(args.config or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        print(f"No config at {config_path}. Run `bridge bootstrap` first.", file=sys.stderr)
        return 1
    cfg = load_config(config_path)
    _setup_logging(cfg.log_level, cfg.log_format)
    store = Store(cfg.db_path)

    print(f"config: {config_path}")
    print(f"db:     {cfg.db_path}")
    daely_token = store.get_token(DAELY_TOKEN_PROVIDER)
    google_token = store.get_token(TOKEN_PROVIDER)
    print(f"daely refresh-token:   {'set' if daely_token else 'MISSING'}")
    print(f"google refresh-token:  {'set' if google_token else 'MISSING'}")
    mappings = store.all_event_mappings()
    print(f"event mappings:        {len(mappings)} (failed: "
          f"{sum(1 for m in mappings if m.failed)})")
    if mappings:
        last = max(mappings, key=lambda m: m.last_synced_at or 0)
        print(f"last sync:             {last.last_synced_at}")

    profile_count = len(cfg.profile_calendar_mapping)
    print(f"profile→calendar map:  {profile_count} entries")
    print(f"fallback calendar:     {cfg.fallback_google_calendar_id or 'unset'}")
    store.close()
    return 0


def cmd_resync(args: argparse.Namespace) -> int:
    """Force a re-patch of existing event mappings on the next sync cycle.

    Sets `last_seen_updated = NULL` on the matching event_mapping rows. The
    next `bridge run` (or its periodic cycle) sees them as "changed" and
    re-issues the Google patch with the current mapper output (e.g. new
    profile colors, refreshed footer, …).

    Optional filters:
      --calendar <daely_calendar_id>    only that calendar's events
      --dry-run                         report what would be reset, do nothing
    """
    config_path = Path(args.config or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        print(f"No config at {config_path}. Run `bridge bootstrap` first.", file=sys.stderr)
        return 1
    cfg = load_config(config_path)
    _setup_logging(cfg.log_level, cfg.log_format)
    store = Store(cfg.db_path)

    cal_id = getattr(args, "calendar", None)
    dry_run = bool(getattr(args, "dry_run", False))

    # Compute the target row count up-front so dry-run is informative.
    if cal_id:
        target_rows = store.event_mappings_for_daely_calendar(cal_id)
        scope_label = f"daely_calendar_id={cal_id!r}"
    else:
        target_rows = store.all_event_mappings()
        scope_label = "all calendars"
    target_count = len(target_rows)

    if dry_run:
        print(f"resync (dry-run): would reset {target_count} mapping(s) "
              f"under {scope_label}")
        if target_count:
            preview = target_rows[:5]
            for m in preview:
                print(f"  {m.daely_id}  → google_event={m.google_event_id} "
                      f"(last_seen_updated={m.last_seen_updated})")
            if target_count > 5:
                print(f"  … and {target_count - 5} more")
        store.close()
        return 0

    affected = store.reset_event_sync_markers(daely_calendar_id=cal_id)
    print(f"resync: reset {affected} mapping(s) under {scope_label}.")
    print("Next `bridge run` cycle will re-patch them with the current "
          "mapper output.")
    log.info(
        "resync.done", run_id=None, scope=scope_label, affected=affected,
    )
    store.close()
    return 0


def cmd_recolor(args: argparse.Namespace) -> int:
    """Alias for `bridge resync` without filters — re-patches every existing
    mapping so the new profile-color/emoji-prefix output reaches Google
    on the next cycle. Same effect as `bridge resync` over all calendars.
    """
    args.calendar = None
    return cmd_resync(args)


# ─────────────── doctor ───────────────

# Status markers and exit-code mapping for cmd_doctor.
DOCTOR_OK = "[OK]  "
DOCTOR_WARN = "[WARN]"
DOCTOR_FAIL = "[FAIL]"
DOCTOR_EXIT_OK = 0
DOCTOR_EXIT_FAIL = 1
DOCTOR_EXIT_WARN = 2


def _doctor_print(marker: str, label: str, message: str) -> None:
    """Format one health-check line. Label gets padded to 26 chars."""
    print(f"{marker} {label:<26}{message}")


def cmd_doctor(
    args: argparse.Namespace,
    *,
    daely_factory: Callable[[BridgeConfig], DaelyClient] | None = None,
    google_factory: Callable[[Store, BridgeConfig], GoogleClient] | None = None,
) -> int:
    """Run a battery of health checks; print results and return an exit code.

    Local-only by default (no network). With `--live` also exercises the
    Daely refresh-token endpoint and a benign Google `list_calendars()`
    call — useful before deploying an update or for diagnosing a stuck sync.

    Exit codes:
      0 = all green
      1 = at least one check FAILed (config missing, tokens absent, live error)
      2 = at least one check WARNed (stale sync, recurring failures, …)
    """
    config_path = Path(args.config or DEFAULT_CONFIG_PATH)
    print(f"bridge doctor — health checks (config: {config_path})\n")

    # ── (1) config load ──
    if not config_path.exists():
        _doctor_print(DOCTOR_FAIL, "config:",
                      f"not found at {config_path}. Run `bridge bootstrap` first.")
        return DOCTOR_EXIT_FAIL
    try:
        cfg = load_config(config_path)
    except Exception as e:
        _doctor_print(DOCTOR_FAIL, "config:", f"invalid: {e}")
        return DOCTOR_EXIT_FAIL
    _doctor_print(DOCTOR_OK, "config:", f"loaded ({cfg.daely_email})")

    # ── (2) database ──
    try:
        store = Store(cfg.db_path)
    except Exception as e:
        _doctor_print(DOCTOR_FAIL, "database:", f"could not open {cfg.db_path}: {e}")
        return DOCTOR_EXIT_FAIL
    # Force a WAL checkpoint right after open so this read-only doctor process
    # sees any writes that the daemon's connection has committed but not yet
    # checkpointed. Belt-and-suspenders: sync._finalize already checkpoints
    # after each cycle, but doing it here covers exec-races during a sync.
    try:
        store.checkpoint()
    except Exception:
        pass  # checkpoint is best-effort; never fail doctor over it
    _doctor_print(DOCTOR_OK, "database:",
                  f"schema v{store.schema_version} at {cfg.db_path}")

    overall = DOCTOR_EXIT_OK
    daely_token = store.get_token(DAELY_TOKEN_PROVIDER)
    google_token = store.get_token(TOKEN_PROVIDER)

    # ── (3) tokens ──
    if daely_token is None:
        _doctor_print(DOCTOR_FAIL, "daely refresh-token:",
                      "missing — run `bridge bootstrap`")
        overall = DOCTOR_EXIT_FAIL
    else:
        _doctor_print(DOCTOR_OK, "daely refresh-token:", "present in store")

    if google_token is None:
        _doctor_print(DOCTOR_FAIL, "google refresh-token:",
                      "missing — run `bridge bootstrap`")
        overall = DOCTOR_EXIT_FAIL
    else:
        _doctor_print(DOCTOR_OK, "google refresh-token:", "present in store")

    # ── (4) mapping table summary ──
    mappings = store.all_event_mappings()
    failed_count = sum(1 for m in mappings if m.failed)
    due_count = len(store.events_due_for_retry())
    line = f"{len(mappings)} total"
    if failed_count:
        line += f", {failed_count} failed"
    if due_count:
        line += f", {due_count} due for retry"
    if failed_count > 0:
        _doctor_print(DOCTOR_WARN, "event mappings:", line)
        if overall == DOCTOR_EXIT_OK:
            overall = DOCTOR_EXIT_WARN
    else:
        _doctor_print(DOCTOR_OK, "event mappings:", line or "0 total")

    # ── (5) last sync age ──
    history = store.recent_sync_history(limit=10)
    now = datetime.now(timezone.utc)
    if not history:
        # Post-restart / fresh-bridge state. Not an error condition —
        # the next cycle will populate. Downgraded to OK so the doctor
        # output isn't yellow during the first 30s of every restart.
        _doctor_print(DOCTOR_OK, "last sync:",
                      "pending — first cycle hasn't completed yet")
    else:
        last = history[0]
        age_s = (now - last.completed_at).total_seconds()
        age_min = int(age_s // 60)
        threshold = cfg.poll_interval_minutes * 2
        age_label = f"{age_min}m ago" if age_min < 1440 else f"{age_min // 60}h ago"
        msg = (f"{age_label}, run {last.run_id} "
               f"(+{last.inserts}/~{last.patches}/-{last.deletes}, "
               f"{last.errors_count} errors)")
        if age_min > threshold + 1:
            msg += f"  [stale: > 2× poll_interval = {threshold}m]"
            _doctor_print(DOCTOR_WARN, "last sync:", msg)
            if overall == DOCTOR_EXIT_OK:
                overall = DOCTOR_EXIT_WARN
        else:
            _doctor_print(DOCTOR_OK, "last sync:", msg)

        # Recent error trend
        with_errors = sum(1 for h in history if h.errors_count > 0)
        if with_errors >= len(history) // 2 and with_errors > 1:
            _doctor_print(DOCTOR_WARN, "sync error trend:",
                          f"{with_errors}/{len(history)} recent runs had errors")
            if overall == DOCTOR_EXIT_OK:
                overall = DOCTOR_EXIT_WARN
        elif with_errors > 0:
            _doctor_print(DOCTOR_OK, "sync error trend:",
                          f"{with_errors}/{len(history)} recent runs had errors "
                          f"(within tolerance)")
        else:
            _doctor_print(DOCTOR_OK, "sync error trend:",
                          f"no errors across last {len(history)} run(s)")

    # ── (6) profile→calendar mapping config ──
    profile_count = len(cfg.profile_calendar_mapping)
    fb = cfg.fallback_google_calendar_id
    if profile_count == 0 and not fb:
        _doctor_print(DOCTOR_FAIL, "config mapping:",
                      "no profile mapping and no fallback — sync would skip all")
        overall = DOCTOR_EXIT_FAIL
    else:
        msg = f"{profile_count} profile entries"
        msg += f", fallback set" if fb else ", no fallback"
        _doctor_print(DOCTOR_OK, "config mapping:", msg)

    # ── (7) optional live checks ──
    if getattr(args, "live", False):
        print("\nLive checks (talking to Daely + Google):")
        live_overall = _doctor_live_checks(
            cfg, store, daely_token, google_token,
            daely_factory=daely_factory, google_factory=google_factory,
        )
        if live_overall == DOCTOR_EXIT_FAIL:
            overall = DOCTOR_EXIT_FAIL
        elif live_overall == DOCTOR_EXIT_WARN and overall == DOCTOR_EXIT_OK:
            overall = DOCTOR_EXIT_WARN

    # ── (8) summary ──
    print()
    if overall == DOCTOR_EXIT_OK:
        print("Overall: OK")
    elif overall == DOCTOR_EXIT_WARN:
        print("Overall: WARN — review the [WARN] lines above")
    else:
        print("Overall: FAIL — fix the [FAIL] lines above")
    if not getattr(args, "live", False):
        print("\nUse `bridge doctor --live` to also try a Daely refresh + "
              "Google list-calendars ping.")
    store.close()
    return overall


def _doctor_live_checks(
    cfg: BridgeConfig,
    store: Store,
    daely_token,  # noqa: ANN001
    google_token,  # noqa: ANN001
    *,
    daely_factory: Callable[[BridgeConfig], DaelyClient] | None,
    google_factory: Callable[[Store, BridgeConfig], GoogleClient] | None,
) -> int:
    """Talk to Daely + Google. Returns OK/WARN/FAIL exit code."""
    out = DOCTOR_EXIT_OK

    # Daely refresh
    if daely_token is None:
        _doctor_print(DOCTOR_WARN, "daely live refresh:",
                      "skipped (no refresh-token in store)")
        if out == DOCTOR_EXIT_OK:
            out = DOCTOR_EXIT_WARN
    else:
        try:
            if daely_factory is not None:
                daely = daely_factory(cfg)
            else:
                daely = DaelyClient(min_pause_seconds=cfg.daely_min_pause_seconds)
            daely.set_tokens(
                access_token=daely_token.access_token,
                refresh_token=daely_token.refresh_token,
            )
            resp = daely.refresh()
            expires = resp.get("expires_in", "?")
            _doctor_print(DOCTOR_OK, "daely live refresh:",
                          f"token refreshed (expires_in={expires}s)")
            # Persist rotated tokens
            if daely.refresh_token:
                store.put_token(
                    provider=DAELY_TOKEN_PROVIDER,
                    refresh_token=daely.refresh_token,
                    access_token=daely.access_token,
                )
            try:
                daely.close()
            except Exception:
                pass
        except DaelyAuthError as e:
            _doctor_print(DOCTOR_FAIL, "daely live refresh:", str(e))
            out = DOCTOR_EXIT_FAIL
        except Exception as e:
            _doctor_print(DOCTOR_FAIL, "daely live refresh:", f"{type(e).__name__}: {e}")
            out = DOCTOR_EXIT_FAIL

    # Google list-calendars ping
    if google_token is None:
        _doctor_print(DOCTOR_WARN, "google live ping:",
                      "skipped (no refresh-token in store)")
        if out == DOCTOR_EXIT_OK:
            out = DOCTOR_EXIT_WARN
    else:
        try:
            if google_factory is not None:
                google = google_factory(store, cfg)
            else:
                creds = GoogleClient.load_credentials(
                    store, cfg.google_oauth_client_secrets_file,
                    scopes=cfg.google_oauth_scopes,
                )
                if creds is None:
                    raise RuntimeError("no Google credentials in store")
                google = GoogleClient(credentials=creds)
            cals = google.list_calendars()
            _doctor_print(DOCTOR_OK, "google live ping:",
                          f"{len(cals)} calendars accessible")
        except Exception as e:
            _doctor_print(DOCTOR_FAIL, "google live ping:",
                          f"{type(e).__name__}: {e}")
            out = DOCTOR_EXIT_FAIL

    return out


# ─────────────────── argparse ───────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bridge")
    parser.add_argument(
        "-c", "--config", help="path to config.yaml (default: ./config.yaml)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("bootstrap", help="interactive setup")

    p_run = sub.add_parser("run", help="run the sync loop (Phase 3d)")
    p_run.add_argument("--once", action="store_true", help="single pass, then exit")

    sub.add_parser("status", help="show what the bridge knows about state")

    p_doctor = sub.add_parser(
        "doctor",
        help="run health checks against config + db (and optionally live "
             "Daely/Google endpoints)",
    )
    p_doctor.add_argument(
        "--live", action="store_true",
        help="also try a Daely refresh + Google list-calendars (requires network)",
    )

    p_resync = sub.add_parser(
        "resync",
        help="reset last_seen_updated on event mappings so the next sync "
             "re-patches them with the current mapper output",
    )
    p_resync.add_argument(
        "--calendar",
        dest="calendar",
        help="only mappings under this Daely calendar id (default: all)",
    )
    p_resync.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="report affected mappings without changing anything",
    )

    p_recolor = sub.add_parser(
        "re-color",
        help="alias for `resync` over all calendars — discoverable shortcut "
             "after a profile-color config change",
    )
    p_recolor.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="report affected mappings without changing anything",
    )

    return parser


COMMANDS = {
    "bootstrap": cmd_bootstrap,
    "run": cmd_run,
    "status": cmd_status,
    "doctor": cmd_doctor,
    "resync": cmd_resync,
    "re-color": cmd_recolor,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return COMMANDS[args.cmd](args)


__all__ = [
    "cmd_bootstrap", "cmd_doctor", "cmd_recolor", "cmd_resync",
    "cmd_run", "cmd_status", "main",
]
