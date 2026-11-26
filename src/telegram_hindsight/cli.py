from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .export_reader import discover_inputs, read_inputs
from .hindsight import HindsightClient, HindsightError
from .models import BuildResult
from .pipeline import build_document
from .state import checkpoint_path, exclusive_lock, read_json, write_json_atomic
from .telegram import TelegramError, collect_daily_updates

DEFAULT_BASE_URL = "https://hindsight.130.210.100.165.sslip.io"
DEFAULT_BANK = "hermes-telegram-staging"


def default_state_dir() -> Path:
    configured = os.environ.get("TELEGRAM_HINDSIGHT_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "antseed-telegram-hindsight"


def add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--chat-id", required=True, help="Numeric Telegram group ID")
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Telegram JSON/JSONL file. Repeat for multiple files.",
    )
    parser.add_argument("--raw-dir", help="Raw archive directory")
    parser.add_argument("--days", type=int, default=30, help="Rolling window in days")
    parser.add_argument("--group-title", help="Human-readable group title")
    parser.add_argument("--now", help="Window end as ISO timestamp, mainly for tests")
    parser.add_argument(
        "--redact-contact-info",
        action="store_true",
        default=os.environ.get("REDACT_CONTACT_INFO", "").lower() == "true",
    )
    parser.add_argument("--state-dir", default=str(default_state_dir()))


def add_hindsight_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base-url", default=os.environ.get("HINDSIGHT_BASE_URL", DEFAULT_BASE_URL)
    )
    parser.add_argument("--api-key", default=os.environ.get("HINDSIGHT_API_KEY"))
    parser.add_argument("--bank", default=os.environ.get("HINDSIGHT_BANK", DEFAULT_BANK))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Replace unchanged content")
    parser.add_argument("--skip-consolidation", action="store_true")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telegram-hindsight",
        description="Deterministic Telegram to Hindsight daily pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preprocess = subparsers.add_parser("preprocess", help="Build a document locally")
    add_source_arguments(preprocess)
    preprocess.add_argument("--output", help="Write the generated document JSON here")

    sync = subparsers.add_parser("sync", help="Replace the Hindsight document")
    add_source_arguments(sync)
    add_hindsight_arguments(sync)

    daily = subparsers.add_parser("daily", help="Collect Bot API updates, then sync")
    add_source_arguments(daily)
    add_hindsight_arguments(daily)
    daily.add_argument(
        "--telegram-token", default=os.environ.get("TELEGRAM_BOT_TOKEN")
    )
    daily.add_argument("--skip-collect", action="store_true")

    collect = subparsers.add_parser("collect", help="Only collect Bot API updates")
    collect.add_argument("--chat-id", required=True)
    collect.add_argument("--state-dir", default=str(default_state_dir()))
    collect.add_argument(
        "--telegram-token", default=os.environ.get("TELEGRAM_BOT_TOKEN")
    )

    restore = subparsers.add_parser("restore", help="Restore a document backup")
    restore.add_argument("--bank", default=os.environ.get("HINDSIGHT_BANK", DEFAULT_BANK))
    restore.add_argument(
        "--base-url", default=os.environ.get("HINDSIGHT_BASE_URL", DEFAULT_BASE_URL)
    )
    restore.add_argument("--api-key", default=os.environ.get("HINDSIGHT_API_KEY"))
    restore.add_argument("backup", help="Backup JSON file")
    return parser


def parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_from_args(args: argparse.Namespace) -> BuildResult:
    state_dir = Path(args.state_dir).expanduser()
    raw_dir = Path(args.raw_dir).expanduser() if args.raw_dir else state_dir / "raw"
    paths = [Path(value).expanduser() for value in args.input] or discover_inputs(raw_dir)
    raw_messages = read_inputs(paths) if paths else []
    return build_document(
        raw_messages,
        chat_id=str(args.chat_id),
        days=args.days,
        now=parse_now(args.now),
        redact_contact_info=args.redact_contact_info,
        run_id=str(uuid.uuid4()),
        group_title=args.group_title,
    )


def require_credentials(args: argparse.Namespace) -> None:
    if not args.api_key:
        raise ValueError("Set HINDSIGHT_API_KEY or pass --api-key")


def backup_document(
    client: HindsightClient,
    bank: str,
    result: BuildResult,
    state_dir: Path,
) -> str | None:
    document = client.get_document(bank, result.document_id)
    if document is None:
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_id = result.document_id.replace(":", "_").replace("/", "_")
    path = state_dir / "backups" / f"{safe_id}-{timestamp}.json"
    write_json_atomic(path, document)
    return str(path)


def sync_from_args(args: argparse.Namespace) -> dict[str, Any]:
    state_dir = Path(args.state_dir).expanduser()
    result = build_from_args(args)
    summary = result.summary()
    summary["bank"] = args.bank
    if args.dry_run:
        summary["status"] = "dry-run"
        return summary
    if result.stats.episodes_created == 0:
        raise ValueError("Refusing to replace Hindsight with an empty document")

    checkpoint_file = checkpoint_path(state_dir, args.chat_id)
    previous = read_json(checkpoint_file, {}) or {}
    if (
        previous.get("content_hash") == result.content_hash
        and previous.get("bank") == args.bank
        and not args.force
    ):
        summary["status"] = "unchanged"
        return summary

    require_credentials(args)
    client = HindsightClient(args.base_url, args.api_key)
    summary["backup"] = backup_document(client, args.bank, result, state_dir)
    summary["retain"] = client.replace_document(args.bank, result)
    if not args.skip_consolidation:
        summary["consolidation"] = client.consolidate(
            args.bank, result.observation_scopes
        )

    write_json_atomic(
        checkpoint_file,
        {
            "status": "completed",
            "bank": args.bank,
            "document_id": result.document_id,
            "content_hash": result.content_hash,
            "metadata": result.metadata,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    summary["status"] = "replaced"
    summary["checkpoint"] = str(checkpoint_file)
    return summary


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "preprocess":
        result = build_from_args(args)
        if args.output:
            output = Path(args.output).expanduser()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(result.content, encoding="utf-8")
            os.chmod(output, 0o600)
        summary = result.summary()
        if args.output:
            summary["output"] = str(output)
        return summary
    if args.command == "sync":
        with exclusive_lock(Path(args.state_dir).expanduser()):
            return sync_from_args(args)
    if args.command == "collect":
        if not args.telegram_token:
            raise ValueError("Set TELEGRAM_BOT_TOKEN or pass --telegram-token")
        with exclusive_lock(Path(args.state_dir).expanduser()):
            return collect_daily_updates(
                args.telegram_token, args.chat_id, Path(args.state_dir).expanduser()
            )
    if args.command == "daily":
        summary: dict[str, Any] = {}
        with exclusive_lock(Path(args.state_dir).expanduser()):
            if not args.skip_collect:
                if not args.telegram_token:
                    raise ValueError("Set TELEGRAM_BOT_TOKEN or pass --telegram-token")
                summary["collect"] = collect_daily_updates(
                    args.telegram_token, args.chat_id, Path(args.state_dir).expanduser()
                )
            summary["sync"] = sync_from_args(args)
        return summary
    if args.command == "restore":
        require_credentials(args)
        backup = read_json(Path(args.backup).expanduser())
        if not backup or not backup.get("original_text"):
            raise ValueError("Backup has no original_text")
        client = HindsightClient(args.base_url, args.api_key)
        return {
            "status": "restored",
            "document_id": backup["id"],
            "retain": client.restore_document(args.bank, backup),
        }
    raise ValueError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        result = run(args)
    except (HindsightError, TelegramError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
