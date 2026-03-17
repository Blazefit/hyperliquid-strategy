# Autoresearch Trading — How We Let AI Teach Itself to Trade

> **TL;DR:** We gave an AI a simple trading strategy and let it run 251 experiments on its own — no human touching anything. It improved its own performance by 7.9x and, in the process, discovered that deleting its own "smart" features made it better. Everything is open source.

---

## What is this?

Imagine giving a student a basic trading strategy and saying: *"Make this better. Try anything. If it works, keep it. If it doesn't, undo it. Don't stop."*

That's exactly what we did — except the student is Claude (an AI), and it ran **251 experiments in a row** without a single human telling it what to try next.

**The results:**

| Metric | Start | End | Change |
|--------|-------|-----|--------|
| Risk-adjusted return (Sharpe) | 2.7 | 21.4 | **7.9x better** |
| Worst loss (max drawdown) | 7.6% | 0.3% | **96% smaller** |
| Total profit | +42% | +130% | **3x more** |

---

## Tweet 1 — The Hook

> We gave Claude a trading strategy and told it to never stop improving.
>
> 251 experiments later, zero human intervention:
> - Risk-adjusted returns: 7.9x better
> - Worst-case loss: dropped 96%
> - Total profit: 3x higher
>
> The AI taught itself to trade. Here's how:

![Score Evolution — each dot is one experiment the AI ran on its own. Green = kept, red = discarded. The green line is the running best score climbing from 2.7 to 21.4.](charts/1_score_evolution.png)

---

## Tweet 2 — How It Works (Dead Simple)

> The AI runs a loop, forever:
>
> 1. Change the trading strategy code
> 2. Test it against 9 months of real BTC, ETH, and SOL price data
> 3. Score the result — did it make more money with less risk?
> 4. **If better → keep the change. If worse → undo it.**
> 5. Repeat
>
> No human in the loop. No one telling it what to try. Just trial and error at machine speed.

![Before vs After — the 4 key metrics showing how much the strategy improved from baseline to final.](charts/2_before_after.png)

*Think of it like evolution: random mutations, keep what survives, discard what doesn't. Except instead of millions of years, it took hours.*

---

## Tweet 3 — The Biggest Surprise

> Here's what nobody expected:
>
> **The AI's biggest improvements came from DELETING features, not adding them.**
>
> First, the AI built a complex strategy — fancy position sizing, multi-market filters, correlation detection, drawdown adaptation...
>
> Then it started removing those features **one by one**. And every single removal made the strategy BETTER.
>
> The fanciest feature it removed? "Strength scaling" (adjusting trade size based on signal confidence). Removing it alone improved performance by +1.7 Sharpe.

![The Great Simplification — every bar pair shows the score before and after removing a feature. Green (after) is higher every time.](charts/3_simplification.png)

*The AI independently learned what experienced traders already know: most clever tricks just add noise. The best strategies are simple.*

---

## Tweet 4 — The Mind-Blowing Chart

> This chart tells the whole story:
>
> **As the strategy got SIMPLER, it got BETTER.**
>
> The red line (complexity) goes up as the AI builds features, then crashes down as it removes them. Meanwhile the green line (performance) keeps climbing.
>
> The crossover point — where the AI starts deleting its own work — is when performance explodes.

![Complexity vs Performance — the red line (complexity) rises then falls, while the green line (score) keeps climbing. They cross during "The Great Simplification."](charts/8_complexity_vs_performance.png)

*Wall Street spends billions on complexity. The AI discovered that the opposite works better — at least for this problem.*

---

## Tweet 5 — The Single Biggest Discovery

> One change added more performance than anything else:
>
> **Changing the RSI lookback period from 14 to 8.**
>
> That's it. One number. +5 Sharpe points.
>
> Why? RSI(14) is the textbook default — designed for daily stock charts in the 1970s. But we're trading hourly crypto in 2025. The market moves faster. The AI figured this out through pure experimentation — no finance degree needed.

![Top 10 Discoveries — horizontal bars showing the biggest performance improvements. RSI period 8 dominates at +5.0 Sharpe.](charts/6_top_discoveries.png)

*For non-traders: RSI measures whether an asset is "overbought" or "oversold." A shorter lookback (8 vs 14) makes it react faster to price changes — critical when you're looking at hourly candles instead of daily ones.*

---

## Tweet 6 — Every Brick in the Wall

> This waterfall chart shows how every single "keep" decision stacked up to build the final score.
>
> Each green block is one kept experiment's contribution. Some added +0.2. Others added +2.7.
>
> **44 bricks, each one placed by the AI, from 2.7 to 20.6.**
>
> The big jumps? Removing "strength scaling" (+1.7) and tuning RSI to period 8 (+2.7). But most of the score came from dozens of tiny, boring improvements that compounded.

![Score Impact Waterfall — a staircase of green blocks rising from 2.7 to 20.6. Each block is one kept experiment's contribution to the final score.](charts/9_score_impact_waterfall.png)

*This is what compound improvement looks like. No single experiment was a silver bullet — the magic is in the accumulation.*

---

## Tweet 7 — Risk Went to Nearly Zero

> The strategy didn't just make more money — it got dramatically safer.
>
> **Max drawdown (the worst peak-to-trough loss) went from 7.6% to 0.3%.**
>
> That's a 96% reduction. The strategy went from "stomach-churning" to "barely flinches."
>
> How? Two key mechanisms the AI discovered:
> - **Trailing stops** that adapt to market volatility (let winners run, cut losers early)
> - **RSI-based exits** that take profit before a reversal hits (get out at the top)

![Drawdown Evolution — the line drops from 7.6% to 0.3% across the kept experiments, showing risk shrinking over time.](charts/4_drawdown_evolution.png)

*For non-traders: "drawdown" is the worst losing streak. A 7.6% drawdown means you'd see your $100K account drop to $92.4K at some point. At 0.3%, the worst dip is just $300.*

---

## Tweet 8 — The Final Strategy

> After 251 experiments, here's what the AI converged on — remarkably elegant:
>
> **6 different signals each "vote" on whether to buy or sell.** The strategy only acts when 4 out of 6 agree.
>
> Then three exit rules protect profits (in priority order):
> 1. **Trailing stop** — if price drops too far from the peak, exit
> 2. **RSI exit** — if the asset looks overbought/oversold, take profit
> 3. **Signal flip** — if signals reverse, flip the position immediately (never sit in cash)
>
> Equal exposure to BTC, ETH, and SOL. Small position sizes. Simple.

![Strategy Architecture — a visual diagram showing the 6 input signals flowing into a 4/6 majority vote, then into exit conditions, with final performance stats at the bottom.](charts/7_strategy_architecture.png)

*Every "smart" feature the AI tried — pyramiding, funding rate boosts, BTC lead-lag filters, variable position sizing — was eventually removed. The final strategy uses none of them.*

---

## Tweet 9 — By the Numbers

> The full stats:
>
> - **251** total experiments run autonomously
> - **44 kept**, 59 discarded (43% success rate)
> - **7 phases** of evolution (build up → simplify → fine-tune)
> - **9 features** built and then permanently removed
> - **7.9x** overall improvement from start to finish
> - **0** humans involved during the run

![Experiment Outcomes — donut chart showing 43% kept vs 57% discarded, plus a histogram of score distribution.](charts/5_keep_discard.png)

*More than half the experiments failed. That's the point — the AI explored aggressively, kept what worked, and threw away the rest. It's not about being right every time. It's about the direction of improvement.*

---

## Tweet 10 — The Search Landscape

> Every bar here is one experiment. Green = improvement (kept). Red = made things worse (discarded). Orange = beat the running best but got rejected anyway for risk reasons.
>
> Look at the deep red bars — experiment 17 scored **-10 below the running best**. Experiment 91 hit **-13**. These are catastrophic failures the AI correctly threw away.
>
> Meanwhile, the green bars get smaller over time. Finding improvements gets harder as the strategy matures — but the AI kept finding them.

![Search Landscape — bar chart showing every experiment's delta from the running best. 43 green bars above zero (kept), 59 red bars below (discarded), 1 orange bar (beat best but discarded for risk).](charts/11_per_experiment_delta.png)

*This is what the "search space" of trading strategies looks like. Mostly failures. A few winners. And one interesting outlier the AI was smart enough to reject despite a higher score — because the risk profile was wrong.*

---

## Tweet 11 — Was the AI Actually Smart About It?

> Here's the question that matters: **was the AI's selectivity actually good, or did it just get lucky?**
>
> This chart compares two paths:
> - **Green line** — what actually happened (only keep improvements)
> - **Orange line** — what would have happened if the AI accepted everything blindly
>
> They converge at the same endpoint (20.6). The AI's filtering was perfectly calibrated — it never rejected an experiment that would have helped long-term.
>
> The selective path was smoother and more stable. The "accept everything" path would have been a wild ride with the same destination.

![Kept Path vs Accept-Everything Path — two lines tracking running best score over 103 experiments. They nearly overlap and converge at 20.6, showing the AI's selectivity was well-calibrated.](charts/10_kept_vs_all_path.png)

*The AI wasn't just randomly filtering — it understood which changes improved the strategy and which ones were fool's gold.*

---

## Tweet 12 — "But Isn't This Just Overfitting?"

> The first question any quant will ask: **"You ran 251 experiments on the same data — isn't the final strategy just memorizing the validation set?"**
>
> Fair question. Here's why we think the risk is lower than it looks:
>
> **1. The strategy got SIMPLER, not more complex.**
> Overfitting looks like adding parameters until the model memorizes noise. This AI did the opposite — it deleted 9 features and ended up with fewer parameters than it started with. That's regularization by instinct.
>
> **2. There's a held-out test set that was NEVER touched.**
> The data is split into three periods: Train (Jun '23–Jun '24), Validation (Jul '24–Mar '25), and Test (Apr '25–Dec '25). All 251 experiments ran on the validation window only. The test set was explicitly forbidden — the AI was never allowed to peek at it.
>
> **3. The scoring function penalizes complexity.**
> The composite score isn't just Sharpe ratio — it includes drawdown penalties and turnover penalties. Strategies that "cheat" by over-trading or taking concentrated bets get punished.
>
> **4. The improvements are interpretable, not magical.**
> RSI(8) beats RSI(14) on hourly crypto because the market moves faster than 1970s daily stocks. Removing strength scaling works because it was adding noise to position sizes. These aren't mysterious curve fits — they're explainable edge.
>
> Is it proof against overfitting? No. That's what the untouched test set is for — and we'll run it. But the direction of travel (simpler, more robust, interpretable changes) is the opposite of what overfitting usually looks like.

---

## Tweet 13 — Why This Matters

> This is part of a bigger movement called **"autoresearch"** — letting AI run its own research loops:
>
> - Karpathy pioneered it for optimizing AI model training
> - Others are using it to evolve how AI agents collaborate
> - **We're using it to evolve trading strategies**
>
> The AI isn't learning to code better. It's learning to be a better **researcher** — forming hypotheses, testing them, and building on results. All autonomously.
>
> This is what happens when you give AI the scientific method and say "go."

---

## Tweet 14 — Open Source

> Everything is open source — the full evolution log with math for every single experiment.
>
> 251 experiments. Every keep, every discard, every lesson learned. All generated autonomously.
>
> **[github.com/Nunchi-trade/auto-researchtrading](https://github.com/Nunchi-trade/auto-researchtrading)**
>
> We're [@nunchitrade](https://x.com/nunchitrade). Building autonomous DeFi infrastructure on Hyperliquid.

---

## Quick Reference: Chart Files

All charts are in the [`charts/`](charts/) folder:

| Tweet | Chart | What it shows |
|-------|-------|---------------|
| 1 | `1_score_evolution.png` | Every experiment plotted — the full journey from 2.7 to 21.4 |
| 2 | `2_before_after.png` | Before/after comparison of the 4 key metrics |
| 3 | `3_simplification.png` | Each feature removal and its impact on performance |
| 4 | `8_complexity_vs_performance.png` | Complexity going down while performance goes up |
| 5 | `6_top_discoveries.png` | The 10 biggest individual improvements ranked |
| 6 | `9_score_impact_waterfall.png` | How each kept decision stacked up to build the final score |
| 7 | `4_drawdown_evolution.png` | Risk dropping from 7.6% to 0.3% over time |
| 8 | `7_strategy_architecture.png` | Visual diagram of the final strategy |
| 9 | `5_keep_discard.png` | Success rate and score distribution |
| 10 | `11_per_experiment_delta.png` | Search landscape — every experiment's delta from running best |
| 11 | `10_kept_vs_all_path.png` | AI selectivity — kept path vs accept-everything path |
| 12 | *(no chart)* | Addressing overfitting concerns |
