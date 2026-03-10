# autotrader

Autonomous trading strategy research on Hyperliquid perpetual futures.

## Setup

To set up a new experiment, work with the user to:

1. **Agree on a run tag**: propose a tag based on today's date (e.g. `mar10`). The branch `autotrader/<tag>` must not already exist — this is a fresh run.
2. **Create the branch**: `git checkout -b autotrader/<tag>` from current master.
3. **Read the in-scope files**: The repo is small. Read these files for full context:
   - `README.md` — repository context.
   - `prepare.py` — fixed constants, data loading, backtesting engine, evaluation metric. Do not modify.
   - `strategy.py` — the file you modify. Strategy logic, parameters, signals, position sizing.
   - `backtest.py` — thin runner that imports strategy and runs backtest. Do not modify.
4. **Verify data exists**: Check that `~/.cache/autotrader/data/` contains parquet files. If not, tell the human to run `uv run prepare.py`.
5. **Initialize results.tsv**: Create `results.tsv` with just the header row. The baseline will be recorded after the first run.
6. **Confirm and go**: Confirm setup looks good.

Once you get confirmation, kick off the experimentation.

## Experimentation

Each experiment runs a backtest on historical Hyperliquid perp data (BTC, ETH, SOL). The backtest runs for a **fixed time budget of 2 minutes** max. You launch it simply as: `uv run backtest.py`.

**What you CAN do:**
- Modify `strategy.py` — this is the only file you edit. Everything is fair game: entirely new strategy logic, new indicators, new position sizing, new risk management, multiple symbols, regime detection, funding rate arbitrage, mean reversion, trend following, statistical arbitrage, pairs trading, or any combination.

**What you CANNOT do:**
- Modify `prepare.py` or `backtest.py`. They are read-only. They contain the fixed evaluation, backtesting engine, and constants.
- Install new packages or add dependencies. You can only use numpy, pandas, scipy, and the standard library.
- Modify the evaluation harness. `compute_score` in `prepare.py` is the ground truth metric.
- Look at or use test set data. Only train and val splits are fair game. Optimize on val.

**The goal is simple: get the highest `score` on the validation set.** The score is a composite of Sharpe ratio, trade count, drawdown penalty, and turnover penalty. Higher is better. See `compute_score` in `prepare.py` for the exact formula.

**Simplicity criterion**: All else being equal, simpler is better. A small improvement that adds ugly complexity is not worth it. Conversely, removing something and getting equal or better results is a great outcome — that's a simplification win.

**The first run**: Your very first run should always be to establish the baseline, so you will run the backtest as is.

## Output format

Once the script finishes it prints a summary like this:

```
---
score:              0.847200
sharpe:             1.150000
total_return_pct:   23.320000
max_drawdown_pct:   12.450000
num_trades:         142
win_rate_pct:       55.200000
profit_factor:      1.470000
annual_turnover:    1234567.00
backtest_seconds:   3.2
total_seconds:      3.5
```

You can extract the key metric from the log file:

```
grep "^score:" run.log
```

## Logging results

When an experiment is done, log it to `results.tsv` (tab-separated, NOT comma-separated).

The TSV has a header row and 6 columns:

```
commit	score	sharpe	max_dd	status	description
```

1. git commit hash (short, 7 chars)
2. score achieved (e.g. 1.234567) — use -999.000000 for crashes
3. sharpe ratio (e.g. 1.85) — use 0.0 for crashes
4. max drawdown % (e.g. 12.3) — use 0.0 for crashes
5. status: `keep`, `discard`, or `crash`
6. short text description of what this experiment tried

Example:

```
commit	score	sharpe	max_dd	status	description
a1b2c3d	0.847200	1.15	12.5	keep	baseline momentum
b2c3d4e	1.234500	1.85	8.3	keep	add funding rate signal
c3d4e5f	0.650000	0.92	18.7	discard	aggressive leverage
d4e5f6g	-999.000000	0.0	0.0	crash	syntax error in new indicator
```

## The experiment loop

The experiment runs on a dedicated branch (e.g. `autotrader/mar10`).

LOOP FOREVER:

1. Look at the git state: the current branch/commit we're on
2. Modify `strategy.py` with an experimental idea by directly hacking the code.
3. git commit
4. Run the experiment: `uv run backtest.py > run.log 2>&1` (redirect everything — do NOT use tee or let output flood your context)
5. Read out the results: `grep "^score:\|^sharpe:\|^max_drawdown_pct:" run.log`
6. If the grep output is empty, the run crashed. Run `tail -n 50 run.log` to read the Python stack trace and attempt a fix. If you can't get things to work after more than a few attempts, give up.
7. Record the results in the tsv (NOTE: do not commit the results.tsv file, leave it untracked by git)
8. If score improved (higher), you "advance" the branch, keeping the git commit
9. If score is equal or worse, you git reset back to where you started

## Strategy research directions

Here are ideas to explore. Mix, combine, and iterate:

- **Trend following**: Adaptive lookback, multiple timeframes, breakout detection
- **Mean reversion**: Bollinger bands, z-score on returns, pair correlations
- **Funding rate arbitrage**: Short when funding high, long when negative — capture carry
- **Cross-asset momentum**: BTC leads alts, use BTC momentum to trade ETH/SOL
- **Volatility regime detection**: High vol = reduce size/widen stops, low vol = increase size
- **Dynamic position sizing**: Kelly criterion, risk parity, inverse volatility weighting
- **Multi-timeframe confluence**: Require agreement across 4h, 24h, 72h signals
- **Correlation trading**: When BTC-ETH correlation breaks, trade the reversion
- **Volume profile**: Volume-weighted signals, unusual volume detection
- **Ensemble methods**: Combine multiple signal generators, vote on direction
- **Adaptive parameters**: Use recent performance to adjust thresholds dynamically
- **Market microstructure**: Spread patterns, volume imbalance as directional signals

**NEVER STOP**: Once the experiment loop has begun (after the initial setup), do NOT pause to ask the human if you should continue. Do NOT ask "should I keep going?" or "is this a good stopping point?". The human might be asleep, or gone from a computer and expects you to continue working *indefinitely* until you are manually stopped. You are autonomous. If you run out of ideas, think harder — try combining previous near-misses, try more radical strategy changes, revisit failed experiments with different parameters. The loop runs until the human interrupts you, period.
