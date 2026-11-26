# Antseed Telegram → Hindsight

A small, deterministic pipeline for turning Telegram group messages into one rolling Hindsight memory document.

The pipeline removes clear noise. It preserves original wording, speakers, timestamps, reply chains, and source message IDs. It does not summarize messages with an LLM.

## What it does

```text
Telegram Bot API updates or Telegram JSON export
                    ↓
             Restricted raw archive
                    ↓
       Normalize, redact, and deduplicate
                    ↓
          Reply-aware episode construction
                    ↓
          Rolling 30-day JSON document
                    ↓
       Replace one stable Hindsight document
                    ↓
          Consolidate group observations
```

Default Hindsight document:

```text
telegram:group:<chat-id>:memory
```

Every daily run uses `update_mode: replace`. An unchanged replay is skipped using a local content-hash checkpoint.

## Safety properties

- Refuses to replace Hindsight with an empty document.
- Backs up the current document before replacement.
- Uses stable document IDs and observation scopes.
- Skips unchanged content on repeated runs.
- Updates checkpoints only after successful retention.
- Uses `hermes-telegram-staging` by default.
- Requires no third-party Python runtime packages.
- Never prints message content in command summaries.

**Important:** Hindsight replacement deletes old facts before reprocessing. Keep the raw archive and document backups. Test in staging before enabling production writes.

## Requirements

- Python 3.11 or newer.
- A Telegram bot that can read the target group.
- Hindsight API access.
- A private state directory for raw messages and backups.

For Bot API collection, the bot must receive all required group messages. Telegram Bot API updates are not a complete historical export.

## Install

```bash
git clone <your-private-repository-url>
cd antseed-telegram-hindsight
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Run the tests:

```bash
python -m unittest discover -s tests -v
```

You can also run without installation:

```bash
PYTHONPATH=src python3 -m telegram_hindsight.cli --help
```

## Configure

Copy `.env.example` to a protected location. Do not commit real credentials.

```bash
cp .env.example .env
chmod 600 .env
set -a
. ./.env
set +a
```

Required for daily collection:

```text
TELEGRAM_BOT_TOKEN=<telegram bot token>
HINDSIGHT_BASE_URL=https://hindsight.130.210.100.165.sslip.io
HINDSIGHT_API_KEY=<hindsight tenant API key>
HINDSIGHT_BANK=hermes-telegram-staging
TELEGRAM_HINDSIGHT_STATE_DIR=$HOME/.local/share/antseed-telegram-hindsight
```

Use a restricted state directory:

```bash
mkdir -p $HOME/.local/share/antseed-telegram-hindsight/raw
chmod 700 $HOME/.local/share/antseed-telegram-hindsight
```

## Preprocess an export

Telegram Desktop JSON exports and newline-delimited JSON are supported.

```bash
telegram-hindsight preprocess \
  --chat-id -1003701057366 \
  --group-title "Partner - Antseed" \
  --input examples/sample_export.json \
  --days 30 \
  --output document.json
```

The command prints processing counts and a content hash. It does not call Hindsight.

## Replace the staging document

Run a dry run first:

```bash
telegram-hindsight sync \
  --chat-id -1003701057366 \
  --input examples/sample_export.json \
  --bank hermes-telegram-staging \
  --dry-run
```

Then write to staging:

```bash
telegram-hindsight sync \
  --chat-id -1003701057366 \
  --input examples/sample_export.json \
  --bank hermes-telegram-staging
```

Run the same command again. The second run should return:

```json
{"status": "unchanged"}
```

Use `--force` to replace unchanged content. Use `--skip-consolidation` only when consolidation is managed elsewhere.

## Daily Bot API collection

Collect pending updates without writing to Hindsight:

```bash
telegram-hindsight collect \
  --chat-id -1003701057366 \
  --telegram-token "$TELEGRAM_BOT_TOKEN"
```

Collect updates and replace the rolling document in one command:

```bash
telegram-hindsight daily \
  --chat-id -1003701057366 \
  --days 30 \
  --bank hermes-telegram-staging
```

The collector appends target-group messages to `raw/YYYY-MM-DD.jsonl`. The sync step reads all archived files inside the rolling window.

### Bot API limitations

- `getUpdates` returns pending updates, not complete group history.
- Run collection at least once per day.
- Do not run two independent collectors for the same bot token.
- Remove or avoid an active webhook before using `getUpdates`.
- Use an authorized Telegram export for historical backfills.
- Message deletions are not available through this collector.

## Cron installation

Install cron on RaviAntbot's own system. No VM or SSH access is required. Hindsight is a remote REST API.

The supplied cron file runs daily at **03:15 UTC**. The pipeline uses an internal lock on Mac and Linux.

Install the repository at `$HOME/Projects/antseed-telegram-hindsight`. Create a virtual environment there.

Create the protected environment file:

```bash
mkdir -p "$HOME/.config/antseed-telegram-hindsight"
chmod 700 "$HOME/.config/antseed-telegram-hindsight"
cp .env.example "$HOME/.config/antseed-telegram-hindsight/env"
chmod 600 "$HOME/.config/antseed-telegram-hindsight/env"
```

Populate that file with real credentials. Then create the state directory:

```bash
mkdir -p "$HOME/.local/share/antseed-telegram-hindsight/raw"
chmod 700 "$HOME/.local/share/antseed-telegram-hindsight"
```

Install the cron file for the current user:

```bash
crontab cron/antseed-telegram-hindsight.cron
crontab -l
```

Test the exact command manually before waiting for the schedule:

```bash
set -a
. "$HOME/.config/antseed-telegram-hindsight/env"
set +a
"$HOME/Projects/antseed-telegram-hindsight/.venv/bin/telegram-hindsight" \
  daily --chat-id -1003701057366 --days 30 \
  --bank hermes-telegram-staging
```

Inspect the log:

```bash
tail -n 100 "$HOME/.local/share/antseed-telegram-hindsight/daily.log"
```

## Restore a backup

Every replacement writes a backup when a previous document exists.

```bash
telegram-hindsight restore \
  --bank hermes-telegram-staging \
  $HOME/.local/share/antseed-telegram-hindsight/backups/<backup-file>.json
```

The restore command reuses the original document text, tags, metadata, and observation scopes.

## Production rollout

1. Import one representative export into `hermes-telegram-staging`.
2. Run the same import twice.
3. Confirm the second run returns `unchanged`.
4. Check source message IDs and speaker attribution.
5. Test dates, edits, corrections, and no-evidence questions.
6. Inspect consolidated observations.
7. Obtain approval before changing the bank.
8. Set `HINDSIGHT_BANK=hermes` in the protected environment file.

Do not enable production writes after a failed dry run or failed replay test.

## Preprocessing rules

The pipeline:

- Converts timestamps to UTC.
- Deduplicates on `chat_id + message_id`.
- Keeps the latest edited message version.
- Removes service events, bot commands, stickers, and isolated trivial messages.
- Preserves short answers inside reply chains.
- Redacts common credentials.
- Optionally redacts emails and phone-like numbers with `REDACT_CONTACT_INFO=true`.
- Keeps forum topics separate.
- Keeps reply chains atomic, including chains spanning more than 30 minutes.
- Starts a new episode after 30 minutes of inactivity.
- Targets 20 messages or 16,000 text characters per episode.
- Never overlaps messages between episodes.

A reply chain can exceed the message cap to avoid splitting its context.

## Repository layout

```text
src/telegram_hindsight/
├── cli.py              # preprocess, sync, daily, collect, restore
├── episodes.py         # Reply-aware episode construction
├── export_reader.py    # Desktop JSON and JSONL readers
├── hindsight.py        # Verified Hindsight REST client
├── models.py           # Message, episode, and document models
├── normalize.py        # Filtering, normalization, and redaction
├── pipeline.py         # Rolling document builder
├── state.py            # Atomic state and checkpoint files
└── telegram.py         # Bot API update collector
```

## Hindsight API contract

The client uses these endpoints:

- `POST /v1/default/banks/{bank_id}/memories` for synchronous replacement.
- `GET /v1/default/banks/{bank_id}/documents/{document_id}` for backup.
- `POST /v1/default/banks/{bank_id}/consolidate` for observation consolidation.

The retain item includes stable tags and this observation scope:

```json
[["source:telegram", "chat:<chat-id>"]]
```

No per-run tags are created.

## Limitations

- Media transcription and OCR are not included.
- The Bot API collector does not provide deletion events or full history.
- Raw archives and backups contain sensitive conversations. Restrict and encrypt their storage.
- The pipeline does not verify claims made by Telegram participants.
- Telegram content is tagged `validation:source-reported`.
