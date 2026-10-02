# MatchLore

**从体育与电竞比赛数据中，挖掘有历史依据的统计亮点。**

[English](README_EN.md) · [部署指南](docs/DEPLOY.zh-CN.md) · [数据格式](docs/DATA_FORMATS.md) · [算法说明](docs/METHOD.md)

MatchLore 将比赛事件与历史记录结合，发现短时爆发、阶段反差、个人纪录和跨场连续表现。提供终端工作台、CLI 和 HTTP API；每条结果携带事实、事件证据及历史比较明细。

## 演示

[▶ 观看 CLI 演示视频](docs/assets/example.mov)

## 能力

| 场景 | 数据适配 | 支持的统计 |
|---|---|---|
| 英超 `epl` | StatsBomb 事件与比赛信息 | 射门、射正、角球、成功过人及单场／跨场模式 |
| Dota 2 `dota2` | OpenDota 已解析比赛 | 玩家归属击杀、死亡、买活、侦查守卫放置、防御塔损失 |

- **历史参考**：按时间和赛季／版本筛选，排除未来及重叠比赛。
- **可核验输出**：原始统计与证据保持一致，支持独立重算。
- **可选 DeepSeek**：另生成解说／转播参考稿，不覆盖原始统计。
- **历史维护**：SQLite 持久化、重复提交幂等、批量导入原子提交。

```text
原始比赛 → 场景适配 → 历史参考 → 模式搜索 → 质量筛选与去重
                                             ↓
                            原始统计与证据 ＋ 可选 AI 参考稿
```

## 快速开始

Python **3.11 或 3.12**。以下为 macOS／Linux 终端命令：

```sh
git clone https://github.com/KawhiQaQ/MatchLore.git
cd MatchLore
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python scripts/make_demo.py --output ./demo-data
matchlore --data ./demo-data
```

最后一条命令打开常驻终端工作台。输入 `/analyze` 选择比赛，`/mode` 切换场景，`/save` 保存结果；`/` 查看命令，Tab 补全，Ctrl+C 取消当前操作。

**演示数据完全由程序生成，不是真实比赛，也不用于证明挖掘质量。** 无需密钥或下载真实数据即可跑通流程。首次分析包含 Numba 编译开销。

### 一次性分析

```sh
matchlore analyze --data ./demo-data --mode epl \
  --match-id epl-demo --minute 30 --max-cards 3 --format text

matchlore analyze --data ./demo-data --mode dota2 \
  --match-id dota-demo --minute 30 --max-cards 3 --output ./result.json
```

`--max-cards` 是输出上限，不保证凑满。没有合格统计时返回空列表。

### 可选 AI 参考稿

```sh
cp .env.example .env
# 在本地 .env 填写 DEEPSEEK_API_KEY
matchlore analyze --data ./demo-data --mode epl \
  --match-id epl-demo --minute 30 --llm deepseek --env-file .env
```

默认不开启模型调用。`.env` 已被 Git 忽略；不要提交真实密钥。

## 输出

| 字段 | 含义 |
|---|---|
| `cards[].original_text` | 确定性原始统计 |
| `cards[].fact` | 结构化事实 |
| `cards[].evidence` | 当前比赛事件证据 |
| `cards[].references` | 历史参考、分母及比较明细 |
| `cards[].broadcast_reference` | 可选模型参考稿及状态 |

参考稿不可用时 `text=null`，原始统计仍然保留。`ready` 表示通过当前校验，**仍需编辑审核**。当前统计文案与终端提示主要为中文。

## 接入真实数据

使用自己的完整比赛文件，按[数据格式](docs/DATA_FORMATS.md)导入历史，再分析新比赛。分析不会自动入库；比赛结束后显式提交 `ingest`。具体步骤、API 调用、备份恢复见[部署指南](docs/DEPLOY.zh-CN.md)。

真实数据不随仓库分发。来源参考：[StatsBomb Open Data](https://github.com/hudl/open-data)、[OpenDota](https://docs.opendota.com/)。使用数据须遵守各自条款。

## 代码结构

```text
highlights/
  _solver/           精确阶段反差搜索与位运算内核
  adapters.py        英超与 Dota 事件适配
  engine.py          候选生成、证据与结果组装
  history.py         跨场连续与历史发生次数
  metrics.py         额外指标与阶段纪录
  quality.py         质量筛选与去重
  narrative.py       参考稿事实约束与语义检查
  llm.py             可选 DeepSeek 调用
  store.py           历史库与幂等入库
  console.py         常驻终端工作台
  api.py             HTTP API
tests/               不依赖私有数据和模型密钥的测试
scripts/make_demo.py  可复现的合成输入
```

## 许可

代码采用 [MIT License](LICENSE)。此许可不覆盖外部比赛数据或模型服务。
