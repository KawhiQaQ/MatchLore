# 部署与使用

[English](DEPLOY.en.md) · [返回 README](../README.md)

## 1. 安装

需要 Python 3.11 或 3.12。macOS／Linux：

```sh
git clone https://github.com/KawhiQaQ/MatchLore.git
cd MatchLore
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
matchlore --version
```

每次打开新终端，进入仓库并执行 `source .venv/bin/activate`。不需要 GPU；首次分析会编译 Numba 内核。

## 2. 先跑通示例

```sh
python scripts/make_demo.py --output ./demo-data
matchlore --data ./demo-data
```

示例是合成数据，每种场景 20 场历史＋1 场待分析比赛，无需密钥。脚本不覆盖现有目录。

进入终端后：`/mode` 选场景，`/analyze` 选比赛，`/save` 保存，`/exit` 退出。也可直接执行：

```sh
matchlore analyze --data ./demo-data --mode dota2 \
  --match-id dota-demo --minute 30 --max-cards 3 --format text
```

## 3. 导入自己的历史库

### 建立独立目录

```sh
export MATCHLORE_DATA="$PWD/partner-data"
matchlore init
```

`init` 仅用于新目录。新终端需重新设置 `MATCHLORE_DATA`，或每次显式传 `--data`。

### 准备文件清单

按[数据格式](DATA_FORMATS.md)准备完整比赛文件。创建 `history.json`，路径相对该清单文件：

```json
[
  {"raw":"events/001.json","metadata":"matches/001.json"},
  {"raw":"events/002.json","metadata":"matches/002.json"}
]
```

上例为英超；Dota 只保留 `raw`。每批 1～1000 场，不混合场景。

```sh
matchlore check --mode epl --manifest ./history.json
matchlore import-history --mode epl --manifest ./history.json
matchlore doctor
matchlore list --mode epl --role all
```

重复的同 ID 同内容记录不再入库；同 ID 不同内容会返回 `match_conflict`。任一条校验失败，整批不提交。

### 导入前检查

检查单场文件，或用上面的 `--manifest` 检查整批；检查不会入库：

```sh
matchlore check --mode epl --raw ./new-events.json --metadata ./new-match.json
matchlore check --mode dota2 --raw ./dota-match.json
```

| 输出 | 含义 |
|---|---|
| `valid` | 必需字段、时间、事件 ID 与最终统计一致性是否通过 |
| `can_ingest` | 当前能否普通入库，包含已有 ID 冲突及撤回状态检查 |
| `errors` | 阻止导入的问题，含字段路径或一致性说明 |
| `warnings` / `coverage` | 缺少哪些可选日志、身份或历史，以及受影响的统计 |
| `existing.revision` | 已有比赛的版本号，更正时使用 |

修复 `errors` 后重新检查。缺少可选日志不会被当成零次事件；对应统计会停用。英超按 StatsBomb 完整事件流契约判定覆盖，不能仅凭缺少某种事件证明该事件发生了零次。`can_ingest=false` 时 CLI 退出码为 2；API 返回 HTTP 200 和检查报告。正式导入仍会重新校验，预检不会锁定数据。

### 分析新比赛

```sh
matchlore analyze --mode epl --raw ./new-events.json \
  --metadata ./new-match.json --phase 2 --minute 30 \
  --max-cards 3 --output ./result.json
```

`phase=2, minute=30` 表示下半场第 30 分钟。Dota 使用 `--mode dota2`，省略 `--metadata` 和 `--phase`。`--max-cards` 范围 1～10，是上限；`--output` 会覆盖同名文件。

### 赛后入库

```sh
matchlore ingest --mode epl --raw ./new-events.json \
  --metadata ./new-match.json
```

分析不自动入库。完整比赛显式入库后，同一个 API 进程会读取新增历史；只有时间及赛季／版本符合条件的记录参与参考。

## 4. 配置可选 DeepSeek

```sh
cp .env.example .env
```

在 `.env` 填写 `DEEPSEEK_API_KEY`。可用 `DEEPSEEK_MODEL` 指定账户可用模型，`DEEPSEEK_TIMEOUT` 为 1～60 秒。

```sh
matchlore analyze --data ./demo-data --mode epl \
  --match-id epl-demo --minute 30 --llm deepseek --env-file .env
```

也可在终端工作台中用 `/llm` 开启。每次非空分析最多调用两次模型，无自动付费重试。模型失败不会删掉原始统计。

| 配置项 | 用途 |
|---|---|
| `--data`／`MATCHLORE_DATA` | 数据目录；显式参数优先 |
| `--env-file`／`MATCHLORE_ENV_FILE` | 模型配置文件；显式参数优先 |
| `DEEPSEEK_API_KEY` | 模型密钥；环境变量覆盖文件值 |
| `MATCHLORE_API_KEY` | 本地 HTTP 服务访问密钥，与模型密钥不同 |

未指定路径时，源码目录存在 `data/` 就使用它，否则使用 `~/.local/share/matchlore/data`。模型配置依次查找显式文件、`MATCHLORE_ENV_FILE`、源码目录 `.env`、`~/.config/matchlore/.env`。

## 5. HTTP API

启动服务：

```sh
matchlore serve --data ./demo-data --port 8765
```

另开终端调用：

```sh
curl http://127.0.0.1:8765/health
curl -X POST http://127.0.0.1:8765/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"mode":"dota2","match_id":"dota-demo","as_of_minute":30,"max_cards":3}'
```

| 接口 | 用途 |
|---|---|
| `GET /openapi.json` | 完整参数与返回契约 |
| `GET /v1/matches?mode=epl&role=all` | 比赛列表 |
| `POST /v1/analyze` | 单次分析 |
| `POST /v1/check` | 单场预检，主体为 `mode`、`raw` 和可选 `metadata` |
| `POST /v1/history/check` | 批量预检，主体为 `{"matches":[单场预检请求]}` |
| `POST /v1/history/status` | 查看比赛版本和变更记录 |
| `POST /v1/history/replace` | 更正或恢复比赛 |
| `POST /v1/history/withdraw` | 撤回比赛 |
| `POST /v1/history/reanalyze` | 按变更版本重算受影响比赛 |
| `POST /v1/ingest` | 完整单场入库 |
| `POST /v1/history/import` | 批量入库，主体为 `{"matches":[单场请求]}` |
| `POST /v1/replay` | 按时间回放 |

API 的 `raw` 是 JSON 内容，不是文件路径。请求上限 16 MiB，大批数据需拆分；每批独立事务。错误返回 `error.code`、`error.message`、`request_id`。

设置 `MATCHLORE_API_KEY` 后，所有接口需 `Authorization: Bearer <token>`；非本地绑定必须设置。当前服务串行执行挖掘／写入，面向本地集成，不作为公网多租户服务直接部署。

## 6. 历史库维护

**备份**：停止所有使用该目录的服务、工作台与写入进程，再复制整个目录。目标目录必须不存在。

```sh
test ! -e ./backup && cp -R "$MATCHLORE_DATA" ./backup
```

保留底座、catalog、比赛文件、SQLite及伴随文件；原始输入、导入清单和密钥若在目录外，另行保管。`demo-export` 不包含滚动入库，不能代替备份。

**恢复**：将完整备份复制到新目录，使用备份时的软件版本核对 ID、数量并抽样分析，再重启服务指向新目录。不要覆盖正在使用的库。

### 更正、撤回与恢复单场

先查看版本：

```sh
matchlore history-status --mode epl --match-id 3754318
```

将返回的 `revision` 填到 `--expected-revision`。下例假设当前为 `0`：

```sh
matchlore replace --mode epl --match-id 3754318 \
  --raw ./corrected-events.json --metadata ./corrected-match.json \
  --expected-revision 0 --reason '供数方修正事件时间'
```

更正必须保持原比赛 ID，并提交完整比赛。撤回使用同样的版本检查：

```sh
matchlore withdraw --mode epl --match-id 3754318 \
  --expected-revision 0 --reason '供数方撤回该场记录'
```

两条示例是独立操作；如果刚执行过更正，撤回需使用更正返回的新 `revision`。遇到 `revision_conflict`，重新查看并核对最新版本后再提交。恢复已撤回比赛也使用 `replace` 和最新版本号；普通 `ingest` 不会恢复撤回记录。

- 更正和撤回均为事务操作，保留原因、时间及前后规范化数据快照；不修改底座 JSON。
- 撤回是逻辑撤回，比赛退出分析和历史参考，审计记录仍保留。
- `affected_matches` 列出库内可能受影响的比赛 ID，考虑变更前后赛季／版本和时间；原始文件临时分析过但未入库的比赛不在清单中。
- 服务和终端后续分析自动读取更新；旧导出文件不会自动改变。

### 重算受影响比赛

使用更正或撤回返回的 `revision`（下例为 `1`），指定本次重算的时间点：

```sh
matchlore reanalyze --mode epl --revision 1 --phase 2 --minute 30 \
  --max-cards 3 --output ./recomputed.json
```

以当前历史库的同一快照批量分析；已撤回比赛不分析，缺少指定半场／分钟的比赛列入 `skipped`。结果位于 `results`，只生成原始统计，不调用 DS。该操作不会复刻过去每次查询的时间点或修改旧文件；如需多个时间点，分别运行。

对应 API 主体：

```json
{"mode":"epl","match_id":"3754318"}
```

上例用于 `/v1/history/status`；撤回额外传 `expected_revision`、`reason`；更正再传 `raw`、`metadata`。重算请求为 `{"mode":"epl","revision":1,"phase":2,"as_of_minute":30,"max_cards":3}`。

终端工作台也支持 `/check`、`/history`、`/replace`、`/withdraw`、`/reanalyze`。首次写入自动升级旧 SQLite 库；升级前备份，并将访问同一数据目录的进程全部更新至 0.9.0 或更新版本后再启动，避免旧版本忽略撤回标记。

## 7. 验证安装

```sh
python -m unittest discover -s tests -v
```

测试不要求真实数据或模型密钥。实际供数接入后还需检查日志完整性、时区、身份映射及输出证据；安装测试不能替代数据验收。
