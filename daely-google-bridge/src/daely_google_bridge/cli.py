"""CLI entry point.

Subcommands:
    bridge bootstrap          Interactive setup: Daely login, Google OAuth, sub-calendars.
    bridge run [--once]       Run the sync loop (Phase 3d — currently stubbed).
    bridge status             Show what the Store knows about state.
    bridge resync <cal_id>    Force a full re-sync of one Daely calendar (Phase 3d — stubbed).

The bootstrap command is the only one that performs network calls in this
phase. `run` and `resync` are stubs returning a "not implemented" message.
"""
from __future__ import annotations

import argparse
import getpass
import shutil
import signal
import sys
from collections.abc import Callable
from pathlib import Path

import structlog

from .config import BridgeConfig, load_config, save_config
from .daely_client import DaelyAuthError, DaelyClient
from .google_client import TOKEN_PROVIDER, GoogleClient
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

    log.info("run.start", once=bool(args.once))
    report = fsync(daely, google, store, cfg)
    _print_report(report)

    if args.once:
        store.close()
        try:
            daely.close()
        except Exception:
            pass
        return 0

    # Background scheduler with graceful shutdown.
    if scheduler_factory is None:
        from apscheduler.schedulers.blocking import BlockingScheduler
        scheduler = BlockingScheduler(timezone="UTC")
    else:
        scheduler = scheduler_factory()

    def _job() -> None:
        try:
            r = isync(daely, google, store, cfg)
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
    print(f"`bridge resync {args.calendar_id}` is not implemented yet.")
    print("It will be added together with Phase 3d (sync.py).")
    return 0


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

    p_resync = sub.add_parser("resync", help="force re-sync of one Daely calendar")
    p_resync.add_argument("calendar_id")

    return parser


COMMANDS = {
    "bootstrap": cmd_bootstrap,
    "run": cmd_run,
    "status": cmd_status,
    "resync": cmd_resync,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return COMMANDS[args.cmd](args)


__all__ = ["cmd_bootstrap", "cmd_run", "cmd_status", "cmd_resync", "main"]
