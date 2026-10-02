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
matchlore import-history --mode epl --manifest ./history.json
matchlore doctor
matchlore list --mode epl --role all
```

重复的同 ID 同内容记录不再入库；同 ID 不同内容会返回 `match_conflict`。任一条校验失败，整批不提交。

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
| `POST /v1/ingest` | 完整单场入库 |
| `POST /v1/history/import` | 批量入库，主体为 `{"matches":[单场请求]}` |
| `POST /v1/replay` | 按时间回放 |

API 的 `raw` 是 JSON 内容，不是文件路径。请求上限 16 MiB，大批数据需拆分；每批独立事务。错误返回 `error.code`、`error.message`、`request_id`。

设置 `MATCHLORE_API_KEY` 后，所有接口需 `Authorization: Bearer <token>`；非本地绑定必须设置。当前服务串行执行挖掘／写入，面向本地集成，不作为公网多租户服务直接部署。

## 6. 历史库维护

**备份：**停止所有使用该目录的服务、工作台与写入进程，再复制整个目录。目标目录必须不存在。

```sh
test ! -e ./backup && cp -R "$MATCHLORE_DATA" ./backup
```

保留底座、catalog、比赛文件、SQLite及伴随文件；原始输入、导入清单和密钥若在目录外，另行保管。`demo-export` 不包含滚动入库，不能代替备份。

**恢复：**将完整备份复制到新目录，使用备份时的软件版本核对 ID、数量并抽样分析，再重启服务指向新目录。不要覆盖正在使用的库。

**更正／撤回：**当前没有单场覆盖或删除命令。备份旧库 → 修正原始文件或清单 → 在新目录初始化 → 重导全部有效历史 → 核验 → 切换目录并重启。不要换 ID 重复录入同一场，也不要直接修改数据库。原始文件不全时先安排迁移。

## 7. 验证安装

```sh
python -m unittest discover -s tests -v
```

测试不要求真实数据或模型密钥。实际供数接入后还需检查日志完整性、时区、身份映射及输出证据；安装测试不能替代数据验收。
