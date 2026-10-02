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
matchlore import-history --mode epl --manifest ./history.json
matchlore doctor
matchlore list --mode epl --role all
```

Identical duplicates return `already_present`. Reusing an ID with different content returns `match_conflict`. Validation errors or conflicts abort the entire batch.

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

**Correct or withdraw a match:** in-place replacement/deletion is not implemented. Back up → correct raw files or the manifest → initialize a new directory → reimport all valid history → verify → switch directories and restart. Do not work around a conflict with a new match ID or directly edit SQLite. If original records are missing, arrange a migration first.

## 7. Verify installation

```sh
python -m unittest discover -s tests -v
```

Tests require neither real records nor model credentials. Real integrations also need checks for log completeness, timestamps, identity mapping, and output evidence.
