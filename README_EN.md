# MatchLore

**Mine statistical highlights from sports and esports data, grounded in historical evidence.**

[中文](README.md) · [Deployment](docs/DEPLOY.en.md) · [Data formats](docs/DATA_FORMATS.md) · [Method](docs/METHOD.md)

MatchLore searches match events and historical records for bursts, phase changes, personal records, and cross-match sequences. It offers a terminal workspace, CLI, and HTTP API. Every result includes structured facts, event evidence, and historical comparisons.

## Features

| Domain | Adapter | Metrics |
|---|---|---|
| Premier League `epl` | StatsBomb events and match metadata | Shots, shots on target, corners, completed dribbles, and within/across-match patterns |
| Dota 2 `dota2` | Parsed OpenDota matches | Player-attributed kills, deaths, buybacks, observer wards placed, and towers lost |

- **Historical context**: time and season/patch filters exclude future and overlapping matches.
- **Auditable results**: original statistics retain their evidence; independent recount checks are included.
- **Optional DeepSeek**: commentary drafts are separate from the original statistics.
- **Persistent history**: SQLite storage, idempotent submissions, and atomic batch imports.

```text
Match data → Adapter → Historical references → Pattern search → Selection
                                                                  ↓
                                    Original facts + optional commentary
```

## Quick start

Requires Python **3.11 or 3.12**. Commands below target macOS/Linux:

```sh
git clone https://github.com/KawhiQaQ/MatchLore.git
cd MatchLore
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python scripts/make_demo.py --output ./demo-data
matchlore --data ./demo-data
```

The last command opens a persistent terminal workspace. Use `/analyze` to select a match, `/mode` to switch domains, `/save` to save results, and `/` for help. Tab completes commands; Ctrl+C cancels the current operation.

**Demo inputs are entirely synthetic.** They exercise the pipeline without API keys or real-data downloads; they are not evidence of real-world insight quality. The first analysis includes Numba compilation time.

### Analyze once

```sh
matchlore analyze --data ./demo-data --mode epl \
  --match-id epl-demo --minute 30 --max-cards 3 --format text

matchlore analyze --data ./demo-data --mode dota2 \
  --match-id dota-demo --minute 30 --max-cards 3 --output ./result.json
```

`--max-cards` is an upper limit. Empty output is valid when no pattern passes selection.

### Optional commentary

```sh
cp .env.example .env
# Set DEEPSEEK_API_KEY in your local .env
matchlore analyze --data ./demo-data --mode epl \
  --match-id epl-demo --minute 30 --llm deepseek --env-file .env
```

Model calls are disabled by default. `.env` is Git-ignored; never commit credentials.

## Output

| Field | Meaning |
|---|---|
| `cards[].original_text` | Deterministic original statistic |
| `cards[].fact` | Structured fact |
| `cards[].evidence` | Supporting events from the current match |
| `cards[].references` | Historical observations, denominators, and comparisons |
| `cards[].broadcast_reference` | Optional commentary text and its status |

Unavailable commentary has `text=null`; original statistics are retained. `ready` means the draft passed current checks, **not that editorial review is unnecessary**. Generated statistics and terminal prompts are currently primarily in Chinese; an English README does not imply English output support.

## Bring your own data

Import historical matches and analyze a new complete match using the documented [source formats](docs/DATA_FORMATS.md). Analysis does not write to history; `ingest` explicitly commits a finished match. See [deployment](docs/DEPLOY.en.md) for import, API, configuration, and maintenance steps.

Real match records are not distributed here. Source references: [StatsBomb Open Data](https://github.com/hudl/open-data) and [OpenDota](https://docs.opendota.com/). Their data terms apply separately.

## Repository layout

```text
highlights/
  _solver/           Exact phase-change search and bit operations
  adapters.py        Domain-specific event adapters
  engine.py          Candidates, evidence, and result assembly
  history.py         Cross-match sequences and occurrence counts
  metrics.py         Additional metrics and phase records
  quality.py         Selection and deduplication
  narrative.py       Semantic constraints and checks
  llm.py             Optional DeepSeek client
  store.py           Persistent history and idempotent ingestion
  console.py         Terminal workspace
  api.py             HTTP API
tests/               Tests without private data or model credentials
scripts/make_demo.py  Reproducible synthetic inputs
```

## Tests

```sh
python -m unittest discover -s tests -v
```

Tests cover search against exhaustive enumeration, time boundaries, historical isolation, evidence recounts, atomic/idempotent ingestion, API contracts, and model failure handling. Synthetic tests validate behavior, not real-world content quality.

## Scope and license

This version analyzes time prefixes of **complete post-match records**. It does not ingest live incremental feeds or push updates. It supports a defined pattern family, not arbitrary natural-language queries. Records are relative to the supplied history, not automatically career-wide. Searched historical frequencies are descriptive, not multiple-testing-adjusted significance probabilities.

Code is released under the [MIT License](LICENSE). External match data and model services are not covered by that license.
