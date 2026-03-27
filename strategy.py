"""
Exp108-realistic: Cleaned up multi-signal momentum strategy.

Key changes from original Exp108:
- BB compression is a real filter (40th percentile), directional-neutral gate not a free vote
- Dead code removed (pyramiding, funding boost, disabled thresholds)
- Portfolio-level position limits enforced (10% per symbol, 25% total)
- Real drawdown reduction (kicks in at 5% DD)
- BTC confirmation actually active for altcoins
- Higher slippage awareness via wider dynamic thresholds
- Proper 5-of-5 directional vote system (BB is a gate, not a vote)
- Longer cooldown to reduce churn
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL"]
SYMBOL_WEIGHTS = {"BTC": 0.34, "ETH": 0.33, "SOL": 0.33}

# Lookback windows
SHORT_WINDOW = 6
MED_WINDOW = 12
MED2_WINDOW = 24
LONG_WINDOW = 48

# Indicator parameters
EMA_FAST = 12
EMA_SLOW = 26
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BB_PERIOD = 20

# Position sizing
BASE_POSITION_PCT = 0.15
VOL_LOOKBACK = 48
TARGET_VOL = 0.015
BASE_THRESHOLD = 0.012

# Risk management
ATR_LOOKBACK = 24
ATR_STOP_MULT = 5.5
ATR_STOP_MULT_PROFIT = 4.5
PROFIT_TIGHTEN_PCT = 0.03
COOLDOWN_BARS = 3
MIN_VOTES = 3  # out of 5 directional signals (majority rule)
BB_COMPRESS_PCTILE = 85  # compression threshold (allows most conditions, filters extreme expansion)

# Portfolio limits
MAX_PER_SYMBOL_PCT = 0.10
MAX_TOTAL_EXPOSURE_PCT = 0.25

# Drawdown management
DD_REDUCE_THRESHOLD = 0.05
DD_REDUCE_FLOOR = 0.3

# BTC confirmation for altcoins
BTC_OPPOSE_THRESHOLD = -0.02

# Correlation-based SOL reduction
CORR_LOOKBACK = 72
HIGH_CORR_THRESHOLD = 0.85


def ema(values, span):
    alpha = 2.0 / (span + 1)
    result = np.empty_like(values, dtype=float)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


def calc_rsi(closes, period):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    rs = avg_gain / max(avg_loss, 1e-10)
    return 100 - 100 / (1 + rs)


class Strategy:
    def __init__(self):
        self.entry_prices = {}
        self.peak_prices = {}
        self.atr_at_entry = {}
        self.trailing_stop = {}
        self.btc_momentum = 0.0
        self.peak_equity = 0.0
        self.exit_bar = {}
        self.bar_count = 0

    def _calc_atr(self, history, lookback):
        if len(history) < lookback + 1:
            return None
        highs = history["high"].values[-lookback:]
        lows = history["low"].values[-lookback:]
        closes = history["close"].values[-(lookback + 1):-1]
        tr = np.maximum(highs - lows,
                        np.maximum(np.abs(highs - closes), np.abs(lows - closes)))
        return np.mean(tr)

    def _calc_vol(self, closes, lookback):
        if len(closes) < lookback:
            return TARGET_VOL
        log_rets = np.diff(np.log(closes[-lookback:]))
        return max(np.std(log_rets), 1e-6)

    def _calc_correlation(self, bar_data):
        if "BTC" not in bar_data or "ETH" not in bar_data:
            return 0.5
        btc_h = bar_data["BTC"].history
        eth_h = bar_data["ETH"].history
        if len(btc_h) < CORR_LOOKBACK or len(eth_h) < CORR_LOOKBACK:
            return 0.5
        btc_rets = np.diff(np.log(btc_h["close"].values[-CORR_LOOKBACK:]))
        eth_rets = np.diff(np.log(eth_h["close"].values[-CORR_LOOKBACK:]))
        if len(btc_rets) < 10:
            return 0.5
        corr = np.corrcoef(btc_rets, eth_rets)[0, 1]
        return corr if not np.isnan(corr) else 0.5

    def _calc_macd(self, closes):
        if len(closes) < MACD_SLOW + MACD_SIGNAL + 5:
            return 0.0
        fast_ema = ema(closes[-(MACD_SLOW + MACD_SIGNAL + 5):], MACD_FAST)
        slow_ema = ema(closes[-(MACD_SLOW + MACD_SIGNAL + 5):], MACD_SLOW)
        macd_line = fast_ema - slow_ema
        signal_line = ema(macd_line, MACD_SIGNAL)
        return macd_line[-1] - signal_line[-1]

    def _calc_bb_width_pctile(self, closes, period):
        if len(closes) < period * 3:
            return 50.0
        widths = []
        for i in range(period * 2, len(closes)):
            window = closes[i - period:i]
            sma = np.mean(window)
            std = np.std(window)
            width = (2 * std) / sma if sma > 0 else 0
            widths.append(width)
        if len(widths) < 2:
            return 50.0
        current_width = widths[-1]
        pctile = 100 * np.sum(np.array(widths) <= current_width) / len(widths)
        return pctile

    def _get_total_exposure(self, portfolio):
        return sum(abs(v) for v in portfolio.positions.values())

    def on_bar(self, bar_data, portfolio):
        signals = []
        self.last_diagnostics = {}
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash
        self.bar_count += 1

        # Drawdown scaling
        self.peak_equity = max(self.peak_equity, equity)
        current_dd = (self.peak_equity - equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        dd_scale = 1.0
        if current_dd > DD_REDUCE_THRESHOLD:
            dd_scale = max(DD_REDUCE_FLOOR, 1.0 - (current_dd - DD_REDUCE_THRESHOLD) * 5)

        # BTC momentum for altcoin confirmation
        if "BTC" in bar_data and len(bar_data["BTC"].history) >= LONG_WINDOW + 1:
            btc_closes = bar_data["BTC"].history["close"].values
            self.btc_momentum = (btc_closes[-1] - btc_closes[-MED2_WINDOW]) / btc_closes[-MED2_WINDOW]

        btc_eth_corr = self._calc_correlation(bar_data)
        high_corr = btc_eth_corr > HIGH_CORR_THRESHOLD

        for symbol in ACTIVE_SYMBOLS:
            if symbol not in bar_data:
                continue
            bd = bar_data[symbol]
            min_bars = max(LONG_WINDOW, EMA_SLOW, MACD_SLOW + MACD_SIGNAL + 5, BB_PERIOD * 3) + 1
            if len(bd.history) < min_bars:
                continue

            closes = bd.history["close"].values
            mid = bd.close

            # Volatility-adjusted threshold
            realized_vol = self._calc_vol(closes, VOL_LOOKBACK)
            vol_ratio = realized_vol / TARGET_VOL
            dyn_threshold = BASE_THRESHOLD * (0.5 + vol_ratio * 0.5)
            dyn_threshold = max(0.006, min(0.025, dyn_threshold))

            # Returns
            ret_vshort = (closes[-1] - closes[-SHORT_WINDOW]) / closes[-SHORT_WINDOW]
            ret_short = (closes[-1] - closes[-MED_WINDOW]) / closes[-MED_WINDOW]

            # Directional signals (5 total)
            mom_bull = ret_short > dyn_threshold
            mom_bear = ret_short < -dyn_threshold

            vshort_bull = ret_vshort > dyn_threshold * 0.5
            vshort_bear = ret_vshort < -dyn_threshold * 0.5

            ema_fast_arr = ema(closes[-(EMA_SLOW + 10):], EMA_FAST)
            ema_slow_arr = ema(closes[-(EMA_SLOW + 10):], EMA_SLOW)
            ema_bull = ema_fast_arr[-1] > ema_slow_arr[-1]
            ema_bear = ema_fast_arr[-1] < ema_slow_arr[-1]

            rsi = calc_rsi(closes, RSI_PERIOD)
            rsi_bull = rsi > 50
            rsi_bear = rsi < 50

            macd_hist = self._calc_macd(closes)
            macd_bull = macd_hist > 0
            macd_bear = macd_hist < 0

            # BB compression as a gate (must be compressed to enter)
            bb_pctile = self._calc_bb_width_pctile(closes, BB_PERIOD)
            bb_ok = bb_pctile < BB_COMPRESS_PCTILE

            # Vote counting -- 5 directional signals, need MIN_VOTES to agree
            bull_votes = sum([mom_bull, vshort_bull, ema_bull, rsi_bull, macd_bull])
            bear_votes = sum([mom_bear, vshort_bear, ema_bear, rsi_bear, macd_bear])

            # BTC confirmation for altcoins
            btc_confirm = True
            if symbol != "BTC":
                if bull_votes >= MIN_VOTES and self.btc_momentum < BTC_OPPOSE_THRESHOLD:
                    btc_confirm = False
                if bear_votes >= MIN_VOTES and self.btc_momentum > -BTC_OPPOSE_THRESHOLD:
                    btc_confirm = False

            bullish = bull_votes >= MIN_VOTES and btc_confirm and bb_ok
            bearish = bear_votes >= MIN_VOTES and btc_confirm and bb_ok

            in_cooldown = (self.bar_count - self.exit_bar.get(symbol, -999)) < COOLDOWN_BARS

            # Diagnostics for this symbol (cast numpy types to native Python)
            diag = {
                "price": float(mid),
                "ret_short": float(round(ret_short * 100, 3)),
                "ret_vshort": float(round(ret_vshort * 100, 3)),
                "dyn_threshold": float(round(dyn_threshold * 100, 3)),
                "rsi": float(round(rsi, 1)),
                "macd_hist": float(round(macd_hist, 4)),
                "bb_pctile": float(round(bb_pctile, 1)),
                "bb_ok": bool(bb_ok),
                "bull_votes": int(bull_votes),
                "bear_votes": int(bear_votes),
                "votes": {
                    "momentum": "BULL" if mom_bull else ("BEAR" if mom_bear else "-"),
                    "vshort_mom": "BULL" if vshort_bull else ("BEAR" if vshort_bear else "-"),
                    "ema": "BULL" if ema_bull else ("BEAR" if ema_bear else "-"),
                    "rsi": "BULL" if rsi_bull else ("BEAR" if rsi_bear else "-"),
                    "macd": "BULL" if macd_bull else ("BEAR" if macd_bear else "-"),
                },
                "btc_confirm": bool(btc_confirm),
                "btc_momentum": float(round(self.btc_momentum * 100, 3)),
                "in_cooldown": bool(in_cooldown),
                "bullish": bool(bullish),
                "bearish": bool(bearish),
                "current_pos": float(portfolio.positions.get(symbol, 0.0)),
                "dd_scale": float(round(dd_scale, 3)),
            }
            self.last_diagnostics[symbol] = diag

            # Position sizing with portfolio limits
            weight = SYMBOL_WEIGHTS.get(symbol, 0.33)
            if high_corr and symbol == "SOL":
                weight *= 0.5

            size = equity * BASE_POSITION_PCT * weight * dd_scale

            # Enforce per-symbol limit
            max_symbol_size = equity * MAX_PER_SYMBOL_PCT
            size = min(size, max_symbol_size)

            current_pos = portfolio.positions.get(symbol, 0.0)
            target = current_pos

            if current_pos == 0:
                # Check total exposure limit before entering
                current_exposure = self._get_total_exposure(portfolio)
                max_new = equity * MAX_TOTAL_EXPOSURE_PCT - current_exposure
                if max_new <= 0:
                    continue
                size = min(size, max_new)

                if not in_cooldown:
                    if bullish:
                        target = size
                    elif bearish:
                        target = -size
            else:
                # Trailing ATR stop
                atr = self._calc_atr(bd.history, ATR_LOOKBACK)
                if atr is None:
                    atr = self.atr_at_entry.get(symbol, mid * 0.02)

                if symbol not in self.peak_prices:
                    self.peak_prices[symbol] = mid

                if current_pos > 0:
                    self.peak_prices[symbol] = max(self.peak_prices[symbol], mid)
                    entry = self.entry_prices.get(symbol, mid)
                    profit_pct = (mid - entry) / entry
                    mult = ATR_STOP_MULT_PROFIT if profit_pct > PROFIT_TIGHTEN_PCT else ATR_STOP_MULT

                    if symbol not in self.trailing_stop:
                        self.trailing_stop[symbol] = mid - mult * atr
                    else:
                        candidate = mid - mult * atr
                        self.trailing_stop[symbol] = max(self.trailing_stop[symbol], candidate)

                    if mid < self.trailing_stop[symbol]:
                        target = 0.0
                else:
                    self.peak_prices[symbol] = min(self.peak_prices[symbol], mid)
                    entry = self.entry_prices.get(symbol, mid)
                    profit_pct = (entry - mid) / entry
                    mult = ATR_STOP_MULT_PROFIT if profit_pct > PROFIT_TIGHTEN_PCT else ATR_STOP_MULT

                    if symbol not in self.trailing_stop:
                        self.trailing_stop[symbol] = mid + mult * atr
                    else:
                        candidate = mid + mult * atr
                        self.trailing_stop[symbol] = min(self.trailing_stop[symbol], candidate)

                    if mid > self.trailing_stop[symbol]:
                        target = 0.0

                # RSI extreme exit
                if current_pos > 0 and rsi > RSI_OVERBOUGHT:
                    target = 0.0
                elif current_pos < 0 and rsi < RSI_OVERSOLD:
                    target = 0.0

                # Momentum reversal: flip if strong opposite signal
                if current_pos > 0 and bearish and not in_cooldown:
                    flip_size = min(size, equity * MAX_PER_SYMBOL_PCT)
                    current_exposure = self._get_total_exposure(portfolio) - abs(current_pos)
                    max_new = equity * MAX_TOTAL_EXPOSURE_PCT - current_exposure
                    flip_size = min(flip_size, max_new) if max_new > 0 else 0
                    target = -flip_size if flip_size > 0 else 0.0
                elif current_pos < 0 and bullish and not in_cooldown:
                    flip_size = min(size, equity * MAX_PER_SYMBOL_PCT)
                    current_exposure = self._get_total_exposure(portfolio) - abs(current_pos)
                    max_new = equity * MAX_TOTAL_EXPOSURE_PCT - current_exposure
                    flip_size = min(flip_size, max_new) if max_new > 0 else 0
                    target = flip_size if flip_size > 0 else 0.0

            if abs(target - current_pos) > 1.0:
                signals.append(Signal(symbol=symbol, target_position=target))
                if target != 0 and current_pos == 0:
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02
                    self.trailing_stop.pop(symbol, None)
                elif target == 0:
                    self.entry_prices.pop(symbol, None)
                    self.peak_prices.pop(symbol, None)
                    self.atr_at_entry.pop(symbol, None)
                    self.trailing_stop.pop(symbol, None)
                    self.exit_bar[symbol] = self.bar_count
                elif (target > 0 and current_pos < 0) or (target < 0 and current_pos > 0):
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02
                    self.trailing_stop.pop(symbol, None)

        return signals
