# Data formats / 数据格式

[中文使用流程](DEPLOY.zh-CN.md) · [English workflow](DEPLOY.en.md)

## Contract

The current adapters accept **complete post-match records** in the source formats below. Partner-specific formats require a tested adapter; matching field names alone does not establish matching event semantics.

- IDs are unique by `(mode, match_id)` within one directory. Separate providers into separate directories or maintain a stable ID mapping.
- Player/team identities must be stable across matches. Display names are not identity keys.
- Do not replace unknown or missing logs with empty/zero logs.
- History must end before the target match starts. EPL partitions by season; Dota partitions by patch and game mode.
- No live partial-record ingestion is implemented.

## Premier League / 英超

Provide an event array as `raw` plus a **single-match metadata object** as `metadata`.

### Metadata

| Field | Requirement |
|---|---|
| `match_id` | Stable ID |
| `competition.competition_id` | `2`; only Premier League is supported by this adapter |
| `season.season_id` | Historical partition |
| `match_date`, `kick_off` | `YYYY-MM-DD` and `HH:MM:SS.sss` |
| `home_team.home_team_id`, `home_team.home_team_name` | Home identity and display name |
| `away_team.away_team_id`, `away_team.away_team_name` | Away identity and display name |
| `home_score`, `away_score` | Final scores; must reconcile with goal events |

### Events

| Field | Requirement |
|---|---|
| `id`, `index` | Unique event ID and source index |
| `period`, `timestamp` | Half `1` / `2`; half-relative `HH:MM:SS.sss` |
| `team.id`, `type.name` | Known team and source event type |
| `player.id`, `player.name` | Required for player-associated statistics |
| `shot.outcome.name` | Shot outcome, e.g. `Goal`, `Saved` |
| `pass.type.name` | `Corner` identifies corners taken |
| `dribble.outcome.name` | `Complete` identifies successful dribbles |

Both halves must have a `Half End` at or after 45 minutes. Do not submit only selected highlights. The adapter uses source kickoff time plus four hours as a conservative eligibility boundary; it does not resolve arbitrary provider time zones. Windows exclude stoppage time, while second-half score context includes first-half stoppage goals.

## Dota 2

Provide a parsed OpenDota match object as `raw`; no separate metadata object is needed.

| Field | Requirement |
|---|---|
| `match_id`, `start_time`, `duration` | Stable ID, nonnegative Unix start time, integer duration of at least 300 seconds for ingestion |
| `patch`, `game_mode` | Historical partition |
| `radiant_win` | Boolean required for ingestion |
| `players` | Exactly ten players |
| `players[].player_slot` | Unique; `0..4` and `128..132` |
| `players[].hero_id` | Hero ID |
| `players[].kills`, `kills_log` | Log length must match the final player kill count |
| `players[].kills_log[].time` | Match-relative seconds; must not exceed duration |

### Optional coverage

| Statistic | Additional fields |
|---|---|
| Cross-match player history | `players[].account_id` |
| Cross-match team history | `radiant_team_id`, `dire_team_id` |
| Death-dependent sequences | `deaths_log` reconciled with `deaths` |
| Buybacks | `buyback_log` reconciled with `buyback_count` |
| Observer wards placed | `obs_log` reconciled with `obs_placed` |
| Towers lost | Valid building events in `objectives` |

Player log fields belong to `players[]`; all log times are seconds. Missing/unverified coverage disables dependent statistics. Kills refer to player-attributed logs, not necessarily every scoreboard kill; wards placed do not imply effective vision. Analysis uses minutes 5 through `min(floor(duration/60), 60)`.

## Obtaining real records

- **StatsBomb**: [Open Data](https://github.com/hudl/open-data), metadata under `data/matches/2/27.json`, events under `data/events/<match_id>.json`. Select one match object from the metadata array. Read the repository terms before using or publishing data-derived results, including its attribution requirements.
- **OpenDota**: [API documentation](https://docs.opendota.com/), complete parsed records from `GET /api/matches/{match_id}`. Log availability varies; a summary-only response is insufficient.

This repository redistributes neither provider's real match data. The local demo generator produces original synthetic fixtures solely for testing the integration.

## Preflight and corrections / 预检与更正

Run `matchlore check --mode <mode> --raw <file>` (plus EPL `--metadata`) before ingestion, or use `--manifest` for a batch. The JSON report distinguishes invalid sources (`valid=false`), storage conflicts (`can_ingest=false`), and optional coverage warnings. Ingestion repeats the checks.

Corrections retain `(mode, match_id)` and supply the complete replacement source. Read `history-status` first and submit its `revision` as `--expected-revision`, with a reason. Withdrawals are logical and auditable; explicit replacement can restore them. See the deployment guides for commands and affected-match reanalysis.
