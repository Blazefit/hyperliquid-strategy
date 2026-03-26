"""
Run backtest across all three splits (train/val/test) to check for overfitting.
If val >> test, the strategy is overfit to the validation window.
"""

import time
import signal as sig
from prepare import load_data, run_backtest, compute_score, TIME_BUDGET

sig.signal(sig.SIGALRM, lambda s, f: (print("TIMEOUT"), exit(1)))
sig.alarm(TIME_BUDGET * 3 + 60)

from strategy import Strategy

for split in ["train", "val", "test"]:
    strategy = Strategy()
    data = load_data(split)
    if not data:
        print(f"\n=== {split.upper()} === NO DATA\n")
        continue

    bars = sum(len(df) for df in data.values())
    print(f"\n=== {split.upper()} === ({bars} bars, {list(data.keys())})")

    result = run_backtest(strategy, data)
    score = compute_score(result)

    print(f"  score:            {score:.4f}")
    print(f"  sharpe:           {result.sharpe:.4f}")
    print(f"  total_return_pct: {result.total_return_pct:.4f}")
    print(f"  max_drawdown_pct: {result.max_drawdown_pct:.4f}")
    print(f"  num_trades:       {result.num_trades}")
    print(f"  win_rate_pct:     {result.win_rate_pct:.2f}")
    print(f"  profit_factor:    {result.profit_factor:.4f}")
    print(f"  backtest_seconds: {result.backtest_seconds:.1f}")
