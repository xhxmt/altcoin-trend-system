# 当前山寨币信号策略

更新时间：2026-04-23

本文整理当前系统正在运行的策略结构。系统只做行情采集、特征计算、排序、候选标记和告警，不自动下单。

## 1. 策略目标

当前策略拆成两条互补通道：

1. `continuation_candidate`
   - 主策略。
   - 目标是捕捉已经形成强趋势、量能确认、且相对强度排在市场前列的 trend continuation。
   - 精度更高，适合作为主要观察和更高权重决策依据。

2. `ignition_candidate`
   - 新增爆发捕获通道。
   - 目标是捕捉 RAVE、EDU 这类“今天突然变强”的 early explosive breakout。
   - 覆盖更早，精度低于主策略，适合作为早期预警、观察仓或低仓位试探依据。

旧的 `trade_candidate` 保持兼容，目前等价于 `continuation_candidate`，不包含爆发通道。

## 2. 当前币种池

实际 `.env` 当前 allowlist：

```text
1000PEPEUSDT,AAVEUSDT,ADAUSDT,ARBUSDT,AVAXUSDT,BNBUSDT,BTCUSDT,DOGEUSDT,EDUUSDT,ENAUSDT,ETHUSDT,GUNUSDT,HIGHUSDT,HYPEUSDT,LINKUSDT,NEARUSDT,OPUSDT,ORDIUSDT,PIEVERSEUSDT,RAVEUSDT,SOLUSDT,SUIUSDT,SUPERUSDT,TAOUSDT,XRPUSDT,ZECUSDT
```

交易所范围：

- Binance USD-M perpetual
- Bybit linear USDT perpetual

系统按交易所分别生成 feature 和 rank，所以 26 个 symbol 对应最多 52 个 exchange-symbol 市场。

## 3. 数据与运行链路

主链路：

```text
exchange market data
  -> alt_core.market_1m
  -> build_snapshot_rows
  -> feature_snapshot
  -> rank_snapshot
  -> alert_events
  -> Telegram alert sender
```

核心模块：

- `src/altcoin_trend/scheduler.py`
  - 读取最近市场数据。
  - 计算 1m/4h/1d 派生特征。
  - 生成 `feature_snapshot` 和 `rank_snapshot`。
  - 调用告警逻辑。

- `src/altcoin_trend/features/scoring.py`
  - 计算最终分数和基础 tier。
  - 当前权重：trend 35%，volume 25%，relative strength 20%，derivatives 15%，quality 5%。

- `src/altcoin_trend/signals/trade_candidate.py`
  - 定义 `continuation_candidate` 和 `ignition_candidate`。

- `src/altcoin_trend/signals/alerts.py`
  - 定义 high value signal。
  - 定义 `explosive_move_early` 事件型告警。

## 4. 核心特征

价格动量：

- `return_1h_pct`
- `return_4h_pct`
- `return_24h_pct`
- `return_7d_pct`
- `return_30d_pct`
- `return_24h_percentile`
- `return_7d_percentile`

趋势结构：

- 4h EMA20 / EMA60
- 1d EMA20 / EMA60
- 4h ADX14
- 4h ATR14
- 20d breakout 标记

量能：

- `volume_ratio_24h`
- `volume_ratio_4h`
- `volume_breakout_score`

相对强度：

- 7d/30d 收益相对 BTC、ETH 的 edge
- 若 BTC/ETH 不可用，回退到交易所内 median 对比
- 输出 `relative_strength_score`

衍生品确认：

- `oi_delta_1h`
- `oi_delta_4h`
- `funding_zscore`
- `taker_buy_sell_ratio`
- 输出 `derivatives_score`

质量和否决：

- `quality_score`
- `veto_reason_codes`

## 5. 总分与基础分层

最终分数：

```text
final_score =
  0.35 * trend_score
  + 0.25 * volume_breakout_score
  + 0.20 * relative_strength_score
  + 0.15 * derivatives_score
  + 0.05 * quality_score
```

基础 tier：

| Tier | 条件 |
|---|---|
| `strong` | `final_score >= 85` |
| `watchlist` | `final_score >= 75` |
| `monitor` | `final_score >= 60` |
| `rejected` | `< 60` 或有 veto |

如果存在 `veto_reason_codes`，基础 tier 直接为 `rejected`。

## 6. Signal v2 Model

Signal v2 把趋势强度、信号等级、风险和可执行性拆开表达，避免再用单一字段同时承担“是不是强势”“是不是早期爆发”“能不能追”的全部含义。

- `final_score`
  - 趋势雷达强度分数，仍然表示整体行情强弱与横截面排名。
- `continuation_grade`
  - 仅表示 continuation 质量，取值为 `A`、`B` 或空。
- `ignition_grade`
  - 仅表示 ignition 质量，取值为 `EXTREME`、`A`、`B` 或空。
- `signal_priority`
  - 告警紧急度，取值 `0` 到 `3`，数值越高越需要立刻关注。
- `chase_risk_score`
  - 0 到 100 的追高风险分数，越高表示越容易晚追。
- `risk_flags`
  - 分类风险标签，例如 extreme move、chase risk、overheat 这类状态；它补充 `chase_risk_score`，不替代数值分数。
- `actionability_score`
  - 机会排序分数，用来衡量当前信号是否值得优先处理。

`trade_candidate` remains compatibility-only and still means continuation is present. It does not include ignition.

下面这些是由 grade 字段组装出来的 canonical signal label；实际存储的 grade 值仍分别保留为对应列里的 `A`、`B` 和 `EXTREME`。

| Grade | Meaning | Use |
|---|---|---|
| `continuation_A` | Strong confirmed continuation | Main watch signal |
| `continuation_B` | Confirmed continuation with weaker confirmation | Secondary watch |
| `ignition_A` | Higher-quality early breakout | Active early alert |
| `ignition_B` | Early breakout warning | Observe, lower priority |
| `ignition_EXTREME` | RAVE-style explosive move | Immediate attention with chase-risk warning |
| `ultra_high_conviction` | Narrow high-conviction continuation breakout | Highest-priority continuation-style alert |

## 7. 主策略：Continuation

当前主策略规则：

```python
def is_continuation_candidate(row):
    return (
        row["return_1h_pct"] >= 6
        and row["return_4h_pct"] >= 10
        and row["return_24h_pct"] >= 12
        and row["volume_ratio_24h"] >= 5
        and row["return_24h_percentile"] >= 0.94
        and row["return_7d_percentile"] >= 0.84
        and row["quality_score"] >= 80
        and not row["veto_reason_codes"]
    )
```

策略含义：

- 要求 1h、4h、24h 同时走强。
- 要求 24h 量能显著放大。
- 要求 24h 和 7d 都处于市场强势分位。
- 更偏向“确认后的强势延续”，不追求抓第一根启动 K。

适用场景：

- HIGH、PIEVERSE、ORDI、GUN 这类已经进入强趋势的行情。
- 更适合作为主观察列表和主要交易决策输入。

## 8. 爆发通道：Ignition

当前爆发捕获规则：

```python
def is_ignition_candidate(row):
    return (
        row["return_1h_pct"] >= 8
        and row["return_24h_pct"] >= 25
        and row["return_24h_percentile"] >= 0.92
        and row["relative_strength_score"] >= 85
        and row["quality_score"] >= 80
        and (
            row["volume_ratio_24h"] >= 1.8
            or row["volume_breakout_score"] >= 35
        )
        and row["derivatives_score"] >= 30
        and not row["veto_reason_codes"]
    )
```

策略含义：

- 不要求 7d 已经强势。
- 允许标的“今天突然变强”。
- 要求 1h 和 24h 同时爆发。
- 要求交易所横截面排名足够靠前。
- 量能确认允许两种路径：
  - 原始 `volume_ratio_24h >= 1.8`
  - 或内部 `volume_breakout_score >= 35`
- 衍生品只做低门槛确认，避免过早过滤爆发段。

适用场景：

- RAVE 这类短期极端爆发行情。
- EDU、SUPER 这类主策略可能较晚识别的启动行情。

## 9. Tier Override

爆发通道不推翻原总分体系，只提供晋级通道：

```python
if is_ignition_candidate(row):
    tier = max_tier(tier, "watchlist")

if (
    is_ignition_candidate(row)
    and row["return_1h_pct"] >= 15
    and row["return_24h_pct"] >= 60
):
    tier = max_tier(tier, "strong")
```

含义：

- 爆发达标后，至少进入 `watchlist`。
- 极端爆发达标后，允许进入 `strong`。
- 这只影响分层展示和告警，不改变 `final_score` 本身。

## 10. 告警结构

当前有两类重要告警逻辑：

1. Tier transition alert
   - 基于 rank/tier 状态变化。
   - 更适合确认型机会。

2. `explosive_move_early`
   - 独立于 tier 存在。
   - 用于防止 tier 分层滞后导致错过早期爆发。

`explosive_move_early` 当前规则：

```python
def is_explosive_move_early_signal(row):
    return (
        (row["return_1h_pct"] >= 12 or row["return_4h_pct"] >= 20)
        and row["return_24h_percentile"] >= 0.97
        and row["relative_strength_score"] >= 90
        and row["quality_score"] >= 80
        and not row["veto_reason_codes"]
    )
```

告警事件类型：

```text
explosive_move_early
```

Signal v2 告警补充：

```text
continuation_confirmed
ignition_detected
ignition_extreme
ultra_high_conviction
exhaustion_risk
```

其中 `ultra_high_conviction` 的告警优先级固定为 `P1`，并按 `symbol + signal family` 做跨交易所去重；同一币在 Binance / Bybit 同时命中时，只保留最优一条事件，但在 payload 里保留 `per_exchange_signals`、`asset_ids` 和 `exchanges`。

## 11. 超高置信规则

`ultra_high_conviction` 不是第三条完全独立的家族，它是建立在 continuation 风格上的更窄版本：要求已经形成明显趋势，并且用更严格的横截面强度、长周期强度和过热上限把“强但太晚”的标的剔掉。

当前生产规则：

```python
def is_ultra_high_conviction_candidate(row):
    return (
        row["return_1h_pct"] >= 12
        and row["return_1h_pct"] <= 40
        and row["return_4h_pct"] >= 38
        and row["return_4h_pct"] <= 110
        and row["return_24h_pct"] >= 50
        and row["return_30d_pct"] >= 65
        and row["volume_ratio_24h"] >= 5
        and row["volume_ratio_24h"] <= 10
        and (
            row["return_24h_rank"] <= 5
            if row.get("return_24h_rank") is not None
            else row["return_24h_percentile"] >= 0.999
        )
        and row["return_7d_percentile"] >= 0.988
        and row["return_30d_percentile"] >= 0.80
        and row["quality_score"] >= 80
        and row["breakout_20d"]
        and not row["veto_reason_codes"]
    )
```

规则解释：

- 1h / 4h / 24h 动量：
  - 要求 `1h >= 12%`、`4h >= 38%`、`24h >= 50%`，确保不是“慢趋势普通强势”，而是明显的强冲段。
  - 同时限制 `1h <= 40%`、`4h <= 110%`，避免把已经过度拉升、极易形成 chase risk 的标的继续标成高置信。
- top-24h rank requirement：
  - 如果生产特征里有 `return_24h_rank`，必须是交易所横截面 `top 5`。
  - 只有在 rank 缺失时，才回退到 `return_24h_percentile >= 0.999` 作为研究近似。
- 7d / 30d strength：
  - `return_7d_percentile >= 0.988`
  - `return_30d_percentile >= 0.80`
  - `return_30d_pct >= 65`
  - 目的不是抓当天突发启动，而是要求这个币本身已经处在更大的强势上下文里。
- breakout / confirmation：
  - 必须有 `breakout_20d=True`。
  - 必须有 `volume_ratio_24h` 在 `[5, 10]` 区间内，既要确认放量，也不要放到极端失真。

和现有家族的关系：

- 相对 `continuation`：
  - 同样偏确认后的强趋势。
  - 但 `ultra_high_conviction` 更窄、更苛刻，并且把 1h/4h 过热上限写进规则，不等价于“更高分 continuation”。
- 相对 `ignition`：
  - `ignition` 允许“今天突然变强”，重点是尽早覆盖。
  - `ultra_high_conviction` 不追求第一根启动，而是要“已确认、仍有空间、且横截面最强”的窄集合。

## 12. Ultra 生产验证口径

`scripts/validate_ultra_signal_production.py` 是当前的可复现评估 harness。它直接从 `alt_core.market_1m` 聚合小时线、重算生产特征、筛出 `ultra_high_conviction` 行，并对每个信号计算未来 1h / 4h / 24h 的路径标签。

固定输入：

- 数据源：`alt_core.market_1m`
- 预热窗口：验证区间前 31 天
- 前瞻窗口：验证区间结束后额外 25 小时，用于补齐未来 24h 标签
- 关键字段：`return_1h_pct`、`return_4h_pct`、`return_24h_pct`、`return_30d_pct`、`volume_ratio_24h`、`return_24h_rank`、`return_24h_percentile`、`return_7d_percentile`、`return_30d_percentile`、`quality_score`、`breakout_20d`、`ultra_high_conviction`

固定输出：

- `summary.json`
  - 现在额外包含 `gate_flow`，用于显示各道 ultra gate 的累计通过数量
- `signals.csv`
- `metadata.json`
- `README.md`

输出目录统一为：

```text
artifacts/autoresearch/<generated-at>-production-ultra-<exchange>-<from>-<to>
```

当前用于对比的生产验证窗口：

```text
2026-01-22T10:00:00+00:00 -> 2026-04-22T10:00:00+00:00
```

2026-04-24 固定窗口 baseline 结果：

Validation window:

```text
7d:  2026-04-15T00:00:00+00:00 -> 2026-04-22T00:00:00+00:00
30d: 2026-03-23T00:00:00+00:00 -> 2026-04-22T00:00:00+00:00
```

7d smoke:

| Exchange | Signals | 24h +10% Precision | +10% before -8% DD |
|---|---:|---:|---:|
| `binance` | 2 | 100.00% | 50.00% |
| `bybit` | 1 | 100.00% | 100.00% |

30d baseline:

| Exchange | Signals | 1h +10% Precision | 4h +10% Precision | 24h +10% Precision | +10% before -8% DD |
|---|---:|---:|---:|---:|---:|
| `binance` | 3 | 100.00% | 100.00% | 100.00% | 33.33% |
| `bybit` | 1 | 100.00% | 100.00% | 100.00% | 100.00% |

合并后：

- `ultra_signal_count=4`
- `precision_24h=1.0`
- `precision_before_dd8=0.5`
- `combined_avg_mfe_24h_pct=52.981898`
- `combined_avg_mae_24h_pct=43.487917`

按固定门槛，这一轮不是 freeze，而是黄色区间：样本数仍然只有 `4`，且 24h MAE 明显高于 `8%` 上限。

2026-04-24 单门调参与诊断结果：

- 先把 `top-24h rank` 从 `top 3` 放松到 `top 5`
- 用同一个 30 天窗口重跑后，Binance 仍然是 `3` 条，Bybit 仍然是 `1` 条
- 这说明当前样本瓶颈不是 rank gate 本身

30d `gate_flow` 诊断：

```text
binance:
window_feature_rows=56115
pass_breakout_20d=393
pass_1h_range=51
pass_24h_momentum=16
pass_30d_return=14
pass_volume_ratio_24h_range=8
pass_top_24h_rank_gate=8
pass_7d_strength_gate=3
pass_30d_strength_gate=3
pass_quality_gate=3

bybit:
window_feature_rows=55835
pass_breakout_20d=391
pass_1h_range=48
pass_24h_momentum=14
pass_30d_return=12
pass_volume_ratio_24h_range=7
pass_top_24h_rank_gate=7
pass_7d_strength_gate=1
pass_30d_strength_gate=1
pass_quality_gate=1
```

这组诊断说明：

- `top-24h rank` 不是当前的主要杀样本门，放松后没有带来任何新增信号
- 真正的大幅压缩先发生在 `breakout_20d` 与 `1h range`
- 在进入 ultra 窄集合后，`7d / 30d strength` 仍然会把候选继续压缩到最终 `3 + 1`

下一步应该优先考虑：

- 先评估是否要放松 `7d / 30d strength` 或 `breakout_20d`
- 不要再继续盲放 `top-24h rank`

命令示例：

```bash
python scripts/validate_ultra_signal_production.py \
  --from 2026-01-22T10:00:00+00:00 \
  --to 2026-04-22T10:00:00+00:00 \
  --exchange binance
```

promotion / retention 口径：

- precision / hit-rate target：
  - 首先看 `precision_1h`、`precision_4h`、`precision_24h`
  - 再看更严格的 `precision_before_dd8`
- false-positive tolerance：
  - 不接受“高 precision 但样本数接近 0”的解释；必须持续跟踪 `ultra_signal_count`
- chase-risk ceiling：
  - 规则层面已经用 `max_return_1h=40`、`max_return_4h=110`、`max_volume_ratio_24h=10` 做第一道上限
  - 线上观察时还要结合 `chase_risk_score` 与 `risk_flags`
- per-symbol / per-exchange dedupe：
  - 告警按 `symbol + signal family` 聚合
  - 最终只发一条最优事件，但必须在 payload 保留各交易所命中状态

## 13. 当前回测精度

回测窗口：

```text
2026-04-07T00:00:00+00:00 -> 2026-04-21T00:00:00+00:00
```

命中定义：

```text
信号后未来 1 小时最高价 >= 信号收盘价 * 1.10
```

14 天结果：

| 通道 | 信号数 | +10% 命中 | +10% 精度 | +5% 精度 | 未来 1h 平均最大涨幅 |
|---|---:|---:|---:|---:|---:|
| `continuation` | 37 | 21 | 56.76% | 83.78% | 14.50% |
| `ignition` | 113 | 55 | 48.67% | 70.80% | 12.81% |
| `ignition_only` | 82 | 36 | 43.90% | 64.63% | 11.81% |

联合信号去重后约：

- 信号数：119
- +10% 命中：57
- +10% 精度：约 47.9%
- +5% 精度：约 70.6%

解释：

- `continuation` 精度更高，但启动更晚。
- `ignition` 覆盖更早，尤其补足 RAVE、EDU、SUPER 这类爆发段。
- `ignition_only` 是新增通道真正贡献的信号，但噪声也最高。

## 14. 当前使用口径

建议按以下方式使用信号：

| 信号状态 | 含义 | 建议用途 |
|---|---|---|
| `continuation_candidate=True` | 主策略确认强势延续 | 主观察，较高优先级 |
| `ignition_candidate=True` 且非 continuation | 爆发早期捕获 | 早期预警，低仓位或观察 |
| `tier=strong` 且 ignition 触发 | 极端爆发，tier override 晋级 | 需要快速检查追高风险 |
| `ultra_high_conviction=True` | 极窄高置信强趋势 | 最高优先级人工复核 |
| 只有高分但无 candidate | 排名靠前但未满足交易候选 | monitor，不应等同交易信号 |
| 有 veto | 风险否决 | 不进入交易候选 |

## 15. 已知问题与下一步优化

1. `volume_breakout_score` 当前主要受 `volume_ratio_4h` 影响。
   - 这解释了 RAVE 原始 24h 量能约 2x，但 volume score 只有约 35-40 的现象。
   - 当前没有直接修改主 volume 公式，以避免影响 continuation 历史行为。

2. `ignition` 精度低于主策略。
   - 下一步可以测试更严格版本：
     - `volume_ratio_24h >= 2.0`
     - 或 `derivatives_score >= 40`
     - 或增加信号后最大回撤过滤。

3. 当前 `ultra_high_conviction` 样本数仍然很小。
   - 这正是该规则的目标，但也意味着不能只看单次 precision。
   - 后续调参必须固定验证窗口，并保留 `metadata.json` / `summary.json` 做横向比较。

4. 当前 1h +10% 命中口径偏向极短线。
   - 后续应补充：
     - 4h / 24h forward return
     - 信号后最大回撤
     - 到主升浪高点的剩余空间
     - 不同 symbol 的分组精度

5. `trade_candidate` 为兼容字段。
   - 后续如果 UI 或告警展示更明确，应直接展示：
     - `continuation_candidate`
     - `ignition_candidate`
     - `explosive_move_early`

## 16. 当前结论

当前系统不是单一交易信号，而是“双通道山寨币趋势雷达”：

- 主策略负责确认型 trend continuation，精度较高。
- 爆发通道负责 early ignition，覆盖更早但噪声更高。
- `ultra_high_conviction` 负责极窄高优先级强趋势，不追第一根，但要求横截面和长周期都足够强。
- tier override 让爆发币不会因为总分体系滞后而停留在 monitor。
- `explosive_move_early` 让事件型预警独立于 tier 状态，降低错过第一段爆发的概率。

实际执行上，应把 `continuation` 和 `ignition` 分开看，不应把所有 candidate 混成一个同等强度的交易信号。
