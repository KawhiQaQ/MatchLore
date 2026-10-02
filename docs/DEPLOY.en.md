# Deployment and usage

[中文](DEPLOY.zh-CN.md) · [README](../README_EN.md)

## 1. Install

Python 3.11 or 3.12, macOS/Linux:

```sh
git clone https://github.com/KawhiQaQ/MatchLore.git
cd MatchLore
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
matchlore --version
```

Activate `.venv` in each new terminal. No GPU is required. The first analysis compiles Numba kernels.

## 2. Run the offline demo

```sh
python scripts/make_demo.py --output ./demo-data
matchlore --data ./demo-data
```

The generator creates fictional data: 20 historical matches and one current match per domain. It refuses an existing destination. No API key is required.

Use `/mode`, `/analyze`, `/save`, and `/exit` in the terminal, or run:

```sh
matchlore analyze --data ./demo-data --mode dota2 \
  --match-id dota-demo --minute 30 --max-cards 3 --format text
```

## 3. Import your history

Create an independent directory:

```sh
export MATCHLORE_DATA="$PWD/partner-data"
matchlore init
```

`init` requires a new directory. Set `MATCHLORE_DATA` again in new terminals, or explicitly pass `--data`.

Prepare complete records in the supported [formats](DATA_FORMATS.md). Create `history.json`; paths are relative to this manifest:

```json
[
  {"raw":"events/001.json","metadata":"matches/001.json"},
  {"raw":"events/002.json","metadata":"matches/002.json"}
]
```

For Dota, use only `raw`. Import 1–1000 records of one domain per batch:

```sh
matchlore check --mode epl --manifest ./history.json
matchlore import-history --mode epl --manifest ./history.json
matchlore doctor
matchlore list --mode epl --role all
```

Identical duplicates return `already_present`. Reusing an ID with different content returns `match_conflict`. Validation errors or conflicts abort the entire batch.

### Check before importing

Check one match, or use `--manifest` above for a batch. Checks do not write to the database:

```sh
matchlore check --mode epl --raw ./new-events.json --metadata ./new-match.json
matchlore check --mode dota2 --raw ./dota-match.json
```

| Output | Meaning |
|---|---|
| `valid` | Required fields, timestamps, event IDs and source reconciliation passed |
| `can_ingest` | Ordinary ingestion is currently possible, including ID conflicts and withdrawals |
| `errors` | Blocking problems with field paths or reconciliation details |
| `warnings` / `coverage` | Missing optional logs, identities or history, and affected statistics |
| `existing.revision` | Current version to use for explicit replacement |

Fix blocking errors and check again. Missing optional logs disable dependent statistics; they are not counted as zero. EPL coverage relies on the complete StatsBomb feed contract; absent event types alone cannot establish zero occurrences. CLI exit code is 2 when `can_ingest=false`; the API returns HTTP 200 with the report. Import validates again; preflight does not lock the data.

### Analyze a new complete match

```sh
matchlore analyze --mode epl --raw ./new-events.json \
  --metadata ./new-match.json --phase 2 --minute 30 \
  --max-cards 3 --output ./result.json
```

`phase=2, minute=30` means minute 30 of the second half. For Dota, use `--mode dota2` and omit `--metadata` and `--phase`. `--max-cards` is an upper bound in 1–10; `--output` overwrites an existing result file.

### Commit the finished match

```sh
matchlore ingest --mode epl --raw ./new-events.json \
  --metadata ./new-match.json
```

Analysis is read-only. Explicitly committed matches become visible to subsequent queries without restarting the API. Time and season/patch eligibility still apply.

## 4. Optional DeepSeek

```sh
cp .env.example .env
```

Set `DEEPSEEK_API_KEY` locally. `DEEPSEEK_MODEL` selects an available model; `DEEPSEEK_TIMEOUT` accepts 1–60 seconds.

```sh
matchlore analyze --data ./demo-data --mode epl \
  --match-id epl-demo --minute 30 --llm deepseek --env-file .env
```

Alternatively, enable `/llm` in the console. Each nonempty analysis uses at most two model calls, with no automatic paid retries. Failed drafts do not remove original statistics.

| Setting | Purpose |
|---|---|
| `--data` / `MATCHLORE_DATA` | Data directory; explicit flag takes precedence |
| `--env-file` / `MATCHLORE_ENV_FILE` | Model configuration file; flag takes precedence |
| `DEEPSEEK_API_KEY` | Model key; environment overrides file values |
| `MATCHLORE_API_KEY` | HTTP service token, separate from the model key |

The default data directory is checkout-local `data/` when present, otherwise `~/.local/share/matchlore/data`. Model config resolution is explicit file, `MATCHLORE_ENV_FILE`, checkout `.env`, then `~/.config/matchlore/.env`.

## 5. HTTP API

```sh
matchlore serve --data ./demo-data --port 8765
```

In another terminal:

```sh
curl http://127.0.0.1:8765/health
curl -X POST http://127.0.0.1:8765/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"mode":"dota2","match_id":"dota-demo","as_of_minute":30,"max_cards":3}'
```

| Endpoint | Purpose |
|---|---|
| `GET /openapi.json` | Full request/response contract |
| `GET /v1/matches?mode=epl&role=all` | List matches |
| `POST /v1/analyze` | Analyze one snapshot |
| `POST /v1/check` | Check one source: `mode`, `raw`, optional `metadata` |
| `POST /v1/history/check` | Check a batch: `{"matches":[check request]}` |
| `POST /v1/history/status` | Current match revision and change log |
| `POST /v1/history/replace` | Correct or restore a match |
| `POST /v1/history/withdraw` | Withdraw a match |
| `POST /v1/history/reanalyze` | Recompute matches affected by a change |
| `POST /v1/ingest` | Commit a complete match |
| `POST /v1/history/import` | Atomic batch: `{"matches":[single-match request]}` |
| `POST /v1/replay` | Replay time prefixes |

API `raw` contains JSON data, not a filesystem path. Request bodies are limited to 16 MiB; split large imports into independent transactions. Errors include `error.code`, `error.message`, and `request_id`.

If `MATCHLORE_API_KEY` is set, every endpoint requires `Authorization: Bearer <token>`. Non-loopback binding requires a token. Mining/writes are serialized; this is a local integration server, not a public multi-tenant service.

## 6. Maintain history

**Backup:** stop all services, consoles, and writers using the directory; copy the whole directory to a new destination:

```sh
test ! -e ./backup && cp -R "$MATCHLORE_DATA" ./backup
```

Include base records, catalogs, SQLite, and sidecar files. Preserve external raw inputs, manifests, and configuration separately. `demo-export` excludes rolling commits and is not a full backup.

**Restore:** copy the full backup to a new directory, use the original software version, verify IDs/counts and sample analyses, then restart the service with the restored path. Never overwrite a running database.

### Correct, withdraw or restore one match

Read the current revision:

```sh
matchlore history-status --mode epl --match-id 3754318
```

Use the returned `revision` as `--expected-revision`. This example assumes `0`:

```sh
matchlore replace --mode epl --match-id 3754318 \
  --raw ./corrected-events.json --metadata ./corrected-match.json \
  --expected-revision 0 --reason 'Provider corrected event timestamps'
```

Replacement requires a complete source with the same match ID. Withdrawal also checks the revision:

```sh
matchlore withdraw --mode epl --match-id 3754318 \
  --expected-revision 0 --reason 'Provider withdrew this record'
```

These are independent examples. After replacement, use its new revision for withdrawal. On `revision_conflict`, inspect the latest state before retrying. Restore a withdrawn match with `replace` and its latest revision; ordinary `ingest` cannot restore it.

- Changes are transactional and retain reasons, timestamps, and before/after normalized snapshots. Base JSON files are unchanged.
- Withdrawal removes a match from analysis and historical reference; audit records remain.
- `affected_matches` lists potentially affected stored IDs using both old/new partitions and time boundaries. Past analyses of uncommitted raw files are not tracked.
- Subsequent analyses see updated data without restarting. Previously exported results remain unchanged.

### Recompute affected matches

Use the change's returned revision (`1` below) and choose a common analysis time:

```sh
matchlore reanalyze --mode epl --revision 1 --phase 2 --minute 30 \
  --max-cards 3 --output ./recomputed.json
```

Recomputes against one current corpus snapshot. Withdrawn matches are excluded; unavailable phases/minutes appear in `skipped`. `results` contains original statistics, with no DS calls. This does not reproduce past query settings or rewrite old exports; run separately for additional times.

API `/v1/history/status` accepts `{"mode":"epl","match_id":"3754318"}`. Withdrawal adds `expected_revision` and `reason`; replacement also adds `raw` and EPL `metadata`. Reanalysis accepts `{"mode":"epl","revision":1,"phase":2,"as_of_minute":30,"max_cards":3}`.

The console provides `/check`, `/history`, `/replace`, `/withdraw` and `/reanalyze`. The first write upgrades legacy SQLite automatically. Back up first and upgrade every process sharing the directory to 0.9.0 or later before restarting; older versions do not honor withdrawal markers.

## 7. Verify installation

```sh
python -m unittest discover -s tests -v
```

Tests require neither real records nor model credentials. Real integrations also need checks for log completeness, timestamps, identity mapping, and output evidence.
