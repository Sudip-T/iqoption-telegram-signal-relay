# """
# ═══════════════════════════════════════════════════════════════════════════
# Manages two output files:

#   stats/daily_stats.txt      — human-readable snapshot, overwritten on each
#                                trade result. Shows SESSION stats (resets each
#                                session start) and DAY TOTAL stats (accumulated
#                                across all sessions today).

#   stats/trading_history.csv  — one row per calendar day, updated in-place.
#                                Accumulates across multiple sessions per day.

# CSV columns:
#   date, trades, signals_received, profit, open_trades, closing_balance,
#   last_updated

# TXT layout:
#   ┌─ SESSION ─┐  trades / signals / P&L / win-rate for this session only
#   ├─ DAY TOTAL┤  accumulated trades / signals / P&L / win-rate since midnight
#   └───────────┘  balance and open trades (live, shared)
# """

# import csv
# import threading
# from datetime import datetime, timezone
# from pathlib import Path


# # ─── Paths ────────────────────────────────────────────────────────────────────

# _STATS_DIR = Path(__file__).parent / "stats"
# _TXT_FILE  = _STATS_DIR / "daily_stats.txt"
# _CSV_FILE  = _STATS_DIR / "trading_history.csv"

# _CSV_HEADERS = [
#     "Date",
#     "Signals",
#     "Trades",
#     "Pending",
#     "Proft/Loss",
#     "Balance Now",
#     "Last Updated",
# ]

# def _ensure_dir() -> None:
#     _STATS_DIR.mkdir(parents=True, exist_ok=True)


# # ─── DayTotals ────────────────────────────────────────────────────────────────

# class DayTotals:
#     """
#     Accumulates day-wide counters across multiple sessions on the same day.
#     Seeded from the CSV at session startup; incremented after each trade result.

#     SessionStats resets to 0 each session — DayTotals never resets within a day.
#     """

#     def __init__(self):
#         self.trades           = 0
#         self.signals_received = 0
#         self.profit           = 0.0
#         self.wins             = 0
#         self.losses           = 0

#     def seed(self, csv_row: dict) -> None:
#         """Populate from a CSV row dict (values are strings — parsed here)."""
#         self.trades           = int(float(csv_row.get("Trades",      0) or 0))
#         self.signals_received = int(float(csv_row.get("Signals",     0) or 0))
#         self.profit           = float(csv_row.get("Proft/Loss",  0.0) or 0.0)
#         # wins/losses not stored in CSV — start from 0 each session
#         self.wins             = 0
#         self.losses           = 0

#     def record(self, pnl: float) -> None:
#         """Call once per trade result with the same pnl passed to SessionStats.record()."""
#         self.profit += pnl
#         if pnl > 0:
#             self.wins   += 1
#         elif pnl < 0:
#             self.losses += 1

#     def on_trade_placed(self) -> None:
#         """Call when a trade is successfully placed (mirrors stats.trades_today += 1)."""
#         self.trades += 1

#     def on_signal_received(self) -> None:
#         """Call when a signal is processed (mirrors stats.signal_received += 1)."""
#         self.signals_received += 1

#     def win_rate(self) -> str:
#         total = self.wins + self.losses
#         if total == 0:
#             return "N/A"
#         return f"{self.wins}/{total}  ({self.wins / total * 100:.0f}%)"


# # ─── StatsReporter ────────────────────────────────────────────────────────────

# class StatsReporter:
#     """
#     Thread-safe reporter.

#     Lifecycle per session:
#         reporter = StatsReporter()
#         day = reporter.load_today()              # seed DayTotals from CSV
#         # ... session runs, trades happen ...
#         reporter.update(session_stats, day)      # after each trade result
#         reporter.flush_day(session_stats, day)   # at session close
#     """

#     def __init__(self):
#         _ensure_dir()
#         self._lock = threading.Lock()

#     # ── Public API ────────────────────────────────────────────────────────────

#     def load_today(self) -> DayTotals:
#         """
#         Read today's CSV row and return a seeded DayTotals.
#         Returns an empty DayTotals (all zeros) if no row exists yet.
#         Call once at session startup.
#         """
#         day = DayTotals()
#         today_str = datetime.now(timezone.utc).date().isoformat()
#         try:
#             if _CSV_FILE.exists():
#                 with open(_CSV_FILE, newline="", encoding="utf-8") as f:
#                     for row in csv.DictReader(f):
#                         if row.get("Date") == today_str:   # match actual header casing
#                             day.seed(row)
#                             break
#         except (OSError, ValueError):
#             pass
#         return day

#     def update(self, stats, day: DayTotals) -> None:
#         """Overwrite TXT and upsert CSV. Call after each trade result."""
#         with self._lock:
#             self._write_txt(stats, day)
#             self._upsert_csv(stats, day)

#     def flush_day(self, stats, day: DayTotals) -> None:
#         """Flush at session close — identical to update()."""
#         with self._lock:
#             self._write_txt(stats, day)
#             self._upsert_csv(stats, day)

#     # ── TXT writer ────────────────────────────────────────────────────────────

#     def _write_txt(self, stats, day: DayTotals) -> None:
#         now   = datetime.now()
#         date  = now.strftime("%A, %d %B %Y")
#         clock = now.strftime("%H:%M:%S")

#         s_sign = "+" if stats.profit_today >= 0 else ""
#         s_pnl  = f"{s_sign}{stats.profit_today:.2f}"
#         s_wr   = self._session_win_rate(stats)

#         d_sign = "+" if day.profit >= 0 else ""
#         d_pnl  = f"{d_sign}{day.profit:.2f}"
#         d_wr   = day.win_rate()

#         lines = [
#             "╔══════════════════════════════════════════════════╗",
#             "║         SIGNAL BRIDGE PRO — DAILY STATS          ║",
#             "╠══════════════════════════════════════════════════╣",
#             f"║  Date          : {date:<32}║",
#             f"║  Last updated  : {clock:<32}║",
#             "╠══════════════════════════════════════════════════╣",
#             "║  ── THIS SESSION ──────────────────────────────  ║",
#             f"║  Trades        : {stats.trades_today:<32}║",
#             f"║  Signals recv  : {stats.signal_received:<32}║",
#             f"║  P&L           : {s_pnl:<32}║",
#             f"║  Win rate      : {s_wr:<32}║",
#             "╠══════════════════════════════════════════════════╣",
#             "║  ── TODAY TOTAL ───────────────────────────────  ║",
#             f"║  Trades        : {day.trades:<32}║",
#             f"║  Signals recv  : {day.signals_received:<32}║",
#             f"║  P&L           : {d_pnl:<32}║",
#             f"║  Win rate      : {d_wr:<32}║",
#             "╠══════════════════════════════════════════════════╣",
#             f"║  Balance       : ${stats.balance:<31,.2f}║",
#             f"║  Open trades   : {stats.pending_trades:<32}║",
#             "╚══════════════════════════════════════════════════╝",
#         ]

#         try:
#             _TXT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
#         except OSError:
#             pass

#     # ── CSV upsert ────────────────────────────────────────────────────────────

#     def _upsert_csv(self, stats, day: DayTotals) -> None:
#         today_str = datetime.now(timezone.utc).date().isoformat()
#         timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

#         new_row = {
#             "Date":             today_str,
#             "Signals": str(day.signals_received),
#             "Trades":           str(day.trades),
#             "Pending":      str(stats.pending_trades),
#             "Proft/Loss":           str(round(day.profit, 2)),
#             "Balance Now":  str(round(stats.balance, 2)),
#             "Last Updated":     timestamp,
#         }

#         try:
#             rows = []
#             if _CSV_FILE.exists():
#                 with open(_CSV_FILE, newline="", encoding="utf-8") as f:
#                     rows = [dict(r) for r in csv.DictReader(f)]

#             today_seen = False
#             updated_rows = []
#             for r in rows:
#                 if r.get("Date") == today_str:
#                     if not today_seen:
#                         updated_rows.append(new_row)
#                         today_seen = True
#                     # drop duplicates silently
#                 else:
#                     updated_rows.append(r)

#             if not today_seen:
#                 updated_rows.append(new_row)

#             with open(_CSV_FILE, "w", newline="", encoding="utf-8") as f:
#                 writer = csv.DictWriter(
#                     f, fieldnames=_CSV_HEADERS, extrasaction="ignore"
#                 )
#                 writer.writeheader()
#                 writer.writerows(updated_rows)

#         except OSError:
#             pass

#     # ── Utility ───────────────────────────────────────────────────────────────

#     @staticmethod
#     def _session_win_rate(stats) -> str:
#         wins   = getattr(stats, "wins_today",   None)
#         losses = getattr(stats, "losses_today", None)
#         if wins is None or losses is None:
#             return "N/A"
#         total = wins + losses
#         if total == 0:
#             return "N/A"
#         return f"{wins}/{total}  ({wins / total * 100:.0f}%)"





# """
# Per-strategy, per-day CSV trade logger.

# One folder per strategy, one CSV file per calendar day inside it:

#     trade_journal/{strategy_source}/{YYYY-MM-DD}.csv

# Columns: time, tf, asset, expiry, investment, return,
#          compunding_total, running_profit, streak, rebuys, rebuys_remaining

# compunding_total is the total payout of THIS trade including the stake
# back — investment + return. "return" alone is just the profit/loss; this
# is what you'd actually have in hand after the trade settles (e.g. $50
# stake + $43 profit = $93 compunding_total on a win; on a loss it's
# whatever's left, i.e. investment + return where return is negative).

# "strategy source" comes straight from TradeSignal.source — the name the
# strategy itself stamped on the signal — so if the engine is ever pointed
# at a different strategy (or multiple), each gets its own file and its own
# running profit / streak instead of everything landing in one bucket.
# """

# import csv
# import os
# import re
# import threading
# from dataclasses import dataclass
# from datetime import datetime
# from typing import Dict, Optional

# FIELDNAMES = ["time", "tf", "asset", "expiry",
#               "investment", "return", "compunding_total",
#               "running_profit", "streak",
#               "rebuys", "rebuys_remaining"]

# _UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


# def _safe_path_part(name: str) -> str:
#     """Turn a strategy source string into something filesystem-safe."""
#     cleaned = _UNSAFE_CHARS.sub("_", (name or "").strip())
#     return cleaned or "unknown_strategy"


# @dataclass
# class _StrategyState:
#     running_profit: float = 0.0
#     streak: int = 0                 # +N win streak, -N loss streak
#     current_date: Optional[str] = None
#     current_path: Optional[str] = None


# class TradeLogger:
#     """Thread-safe. Call log_trade() once per completed trade outcome."""

#     def __init__(self, log_dir: str = "trade_journal"):
#         self.log_dir = log_dir
#         os.makedirs(self.log_dir, exist_ok=True)
#         self._lock = threading.Lock()
#         self._state: Dict[str, _StrategyState] = {}

#     def _ensure_file(self, source: str, state: _StrategyState) -> str:
#         """Roll over to a new file automatically when the date changes."""
#         today = datetime.now().strftime("%Y-%m-%d")
#         if state.current_date != today:
#             state.current_date = today
#             strategy_dir = os.path.join(self.log_dir, _safe_path_part(source))
#             os.makedirs(strategy_dir, exist_ok=True)
#             state.current_path = os.path.join(strategy_dir, f"{today}.csv")
#             if not os.path.exists(state.current_path):
#                 with open(state.current_path, "w", newline="", encoding="utf-8") as f:
#                     csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
#         return state.current_path

#     def log_trade(self, source: str, timeframe: int, expiry: int,
#                   asset: str, investment: float, result: float,
#                   rebuys: int = 0, rebuys_remaining="∞") -> None:
#         """Append one completed trade's outcome to today's CSV for `source`.

#         source            strategy identifier (TradeSignal.source)
#         timeframe         candle timeframe in seconds
#         expiry            trade expiry (same unit as PairConfig.expiry)
#         asset             traded asset symbol
#         investment        stake placed on this trade
#         result            profit/loss amount (positive = win, negative =
#                           loss, 0 = tie)
#         rebuys            how many times the stake has dropped back to the
#                           starting balance so far (caller-tracked, e.g.
#                           from CompoundingManager.status()["restarts"])
#         rebuys_remaining  MAX_REBUY - rebuys, or "∞" if MAX_REBUY is 0
#                           (caller-tracked, e.g. from
#                           CompoundingManager.status()["rebuys_remaining"])
#         """
#         with self._lock:
#             state = self._state.setdefault(source, _StrategyState())
#             path = self._ensure_file(source, state)

#             state.running_profit += result
#             if result > 0:
#                 state.streak = state.streak + 1 if state.streak >= 0 else 1
#             elif result < 0:
#                 state.streak = state.streak - 1 if state.streak <= 0 else -1
#             # ties leave the streak unchanged

#             compunding_total = investment + result

#             row = {
#                 "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
#                 "tf": timeframe,
#                 "asset": asset,
#                 "expiry": expiry,
#                 "investment": round(investment, 2),
#                 "return": round(result, 2),
#                 "compunding_total": round(compunding_total, 2),
#                 "running_profit": round(state.running_profit, 2),
#                 "streak": state.streak,
#                 "rebuys": rebuys,
#                 "rebuys_remaining": rebuys_remaining,
#             }
#             with open(path, "a", newline="", encoding="utf-8") as f:
#                 csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(row)




"""
Per-strategy, per-day CSV trade logger  +  per-strategy stats reporter.

One folder per strategy; inside it:

    trade_journal/{strategy_source}/{YYYY-MM-DD}.csv    (unchanged — raw trades)
    trade_journal/{strategy_source}/daily_stats.txt     (NEW — live snapshot)
    trade_journal/{strategy_source}/trading_history.csv (NEW — one row per day)

The stats reporter mirrors iengine._stats_reporter but is scoped to a
single strategy: every log_trade() call also refreshes that strategy's
daily_stats.txt and upserts today's row in its trading_history.csv.
Day totals are seeded back from trading_history.csv on the first trade of
the day, so restarts within the same day keep accumulating instead of
resetting.

Columns of the per-day trade CSV: time, tf, asset, expiry, investment,
return, compunding_total, running_profit, streak, rebuys, rebuys_remaining

compunding_total is the total payout of THIS trade including the stake
back — investment + return. "return" alone is just the profit/loss; this
is what you'd actually have in hand after the trade settles (e.g. $50
stake + $43 profit = $93 compunding_total on a win; on a loss it's
whatever's left, i.e. investment + return where return is negative).

"strategy source" comes straight from TradeSignal.source — the name the
strategy itself stamped on the signal — so if the engine is ever pointed
at a different strategy (or multiple), each gets its own file and its own
running profit / streak instead of everything landing in one bucket.
"""

import csv
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

FIELDNAMES = ["time", "tf", "asset", "expiry",
              "investment", "return", "compunding_total",
              "running_profit", "streak",
              "rebuys", "rebuys_remaining"]

# ── stats reporter files (per strategy folder) ──────────────────────────
_STATS_TXT_NAME = "daily_stats.txt"
_HISTORY_CSV_NAME = "trading_history.csv"

_HISTORY_HEADERS = [
    "Date",
    "Trades",
    "Wins",
    "Losses",
    "Win Rate",
    "Proft/Loss",
    "Volume",            # total invested today
    "Best Streak",
    "Worst Streak",
    "Running Profit",    # all-time running profit at last update
    "Last Updated",
]

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_path_part(name: str) -> str:
    """Turn a strategy source string into something filesystem-safe."""
    cleaned = _UNSAFE_CHARS.sub("_", (name or "").strip())
    return cleaned or "unknown_strategy"


# ─────────────────────────────────────────────────────────────────────────
#  Per-strategy day totals (the stats-reporter accumulator)
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class _DayStats:
    """Accumulates one strategy's counters for the current calendar day.

    Seeded back from that strategy's trading_history.csv on the first
    trade after a restart, so same-day restarts keep accumulating.
    """
    date: Optional[str] = None
    trades: int = 0
    wins: int = 0
    losses: int = 0
    ties: int = 0
    profit: float = 0.0
    volume: float = 0.0            # sum of investments
    best_streak: int = 0
    worst_streak: int = 0

    def seed(self, row: dict) -> None:
        """Populate from a trading_history.csv row (string values)."""
        try:
            self.trades = int(float(row.get("Trades", 0) or 0))
            self.wins = int(float(row.get("Wins", 0) or 0))
            self.losses = int(float(row.get("Losses", 0) or 0))
            self.profit = float(row.get("Proft/Loss", 0.0) or 0.0)
            self.volume = float(row.get("Volume", 0.0) or 0.0)
            self.best_streak = int(float(row.get("Best Streak", 0) or 0))
            self.worst_streak = int(float(row.get("Worst Streak", 0) or 0))
            self.ties = max(0, self.trades - self.wins - self.losses)
        except (ValueError, TypeError):
            pass  # malformed row — start clean rather than crash

    def record(self, investment: float, result: float, streak: int) -> None:
        self.trades += 1
        self.volume += investment
        self.profit += result
        if result > 0:
            self.wins += 1
        elif result < 0:
            self.losses += 1
        else:
            self.ties += 1
        self.best_streak = max(self.best_streak, streak)
        self.worst_streak = min(self.worst_streak, streak)

    def win_rate(self) -> str:
        total = self.wins + self.losses
        if total == 0:
            return "N/A"
        return f"{self.wins}/{total}  ({self.wins / total * 100:.0f}%)"


@dataclass
class _StrategyState:
    running_profit: float = 0.0
    streak: int = 0                 # +N win streak, -N loss streak
    current_date: Optional[str] = None
    current_path: Optional[str] = None
    day: _DayStats = field(default_factory=_DayStats)   # stats-reporter totals


class TradeLogger:
    """Thread-safe. Call log_trade() once per completed trade outcome."""

    def __init__(self, log_dir: str = "trade_journal"):
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._state: Dict[str, _StrategyState] = {}

    # ── unchanged plumbing ────────────────────────────────────────────────

    def _strategy_dir(self, source: str) -> str:
        return os.path.join(self.log_dir, _safe_path_part(source))

    def _ensure_file(self, source: str, state: _StrategyState) -> str:
        """Roll over to a new file automatically when the date changes."""
        today = datetime.now().strftime("%Y-%m-%d")
        if state.current_date != today:
            state.current_date = today
            strategy_dir = self._strategy_dir(source)
            os.makedirs(strategy_dir, exist_ok=True)
            state.current_path = os.path.join(strategy_dir, f"{today}.csv")
            if not os.path.exists(state.current_path):
                with open(state.current_path, "w", newline="", encoding="utf-8") as f:
                    csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
            # new day (or first trade after restart) → (re)load day stats
            self._load_day_stats(source, state, today)
        return state.current_path

    def log_trade(self, source: str, timeframe: int, expiry: int,
                  asset: str, investment: float, result: float,
                  rebuys: int = 0, rebuys_remaining="∞") -> None:
        """Append one completed trade's outcome to today's CSV for `source`.

        source            strategy identifier (TradeSignal.source)
        timeframe         candle timeframe in seconds
        expiry            trade expiry (same unit as PairConfig.expiry)
        asset             traded asset symbol
        investment        stake placed on this trade
        result            profit/loss amount (positive = win, negative =
                          loss, 0 = tie)
        rebuys            how many times the stake has dropped back to the
                          starting balance so far (caller-tracked, e.g.
                          from CompoundingManager.status()["restarts"])
        rebuys_remaining  MAX_REBUY - rebuys, or "∞" if MAX_REBUY is 0
                          (caller-tracked, e.g. from
                          CompoundingManager.status()["rebuys_remaining"])
        """
        with self._lock:
            state = self._state.setdefault(source, _StrategyState())
            path = self._ensure_file(source, state)

            state.running_profit += result
            if result > 0:
                state.streak = state.streak + 1 if state.streak >= 0 else 1
            elif result < 0:
                state.streak = state.streak - 1 if state.streak <= 0 else -1
            # ties leave the streak unchanged

            compunding_total = investment + result

            row = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "tf": timeframe,
                "asset": asset,
                "expiry": expiry,
                "investment": round(investment, 2),
                "return": round(result, 2),
                "compunding_total": round(compunding_total, 2),
                "running_profit": round(state.running_profit, 2),
                "streak": state.streak,
                "rebuys": rebuys,
                "rebuys_remaining": rebuys_remaining,
            }
            with open(path, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(row)

            # ── NEW: per-strategy stats reporter ──────────────────────────
            state.day.record(investment, result, state.streak)
            self._write_stats(source, state,
                              rebuys=rebuys,
                              rebuys_remaining=rebuys_remaining)

    # ─────────────────────────────────────────────────────────────────────
    #  Stats reporter (per strategy) — TXT snapshot + daily history CSV
    # ─────────────────────────────────────────────────────────────────────

    def _load_day_stats(self, source: str, state: _StrategyState,
                        today: str) -> None:
        """Seed today's totals from this strategy's trading_history.csv,
        so a same-day restart continues accumulating."""
        state.day = _DayStats(date=today)
        history = os.path.join(self._strategy_dir(source), _HISTORY_CSV_NAME)
        try:
            if os.path.exists(history):
                with open(history, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        if row.get("Date") == today:
                            state.day.seed(row)
                            break
                # running profit continues across restarts too: resume from
                # today's row if the in-memory value is still fresh (0.0)
                if state.running_profit == 0.0:
                    try:
                        with open(history, newline="", encoding="utf-8") as f:
                            for row in csv.DictReader(f):
                                if row.get("Date") == today:
                                    state.running_profit = float(
                                        row.get("Running Profit", 0.0) or 0.0)
                                    break
                    except (ValueError, TypeError):
                        pass
        except OSError:
            pass

    def _write_stats(self, source: str, state: _StrategyState,
                     rebuys: int = 0, rebuys_remaining="∞") -> None:
        """Overwrite daily_stats.txt and upsert trading_history.csv for one
        strategy. Called with self._lock already held."""
        strategy_dir = self._strategy_dir(source)
        os.makedirs(strategy_dir, exist_ok=True)
        self._write_txt(source, strategy_dir, state, rebuys, rebuys_remaining)
        self._upsert_history(strategy_dir, state)

    def _write_txt(self, source: str, strategy_dir: str,
                   state: _StrategyState, rebuys, rebuys_remaining) -> None:
        day = state.day
        now = datetime.now()
        date = now.strftime("%A, %d %B %Y")
        clock = now.strftime("%H:%M:%S")

        d_sign = "+" if day.profit >= 0 else ""
        d_pnl = f"{d_sign}{day.profit:.2f}"
        r_sign = "+" if state.running_profit >= 0 else ""
        r_pnl = f"{r_sign}{state.running_profit:.2f}"
        streak = (f"+{state.streak}" if state.streak > 0 else str(state.streak))

        title = _safe_path_part(source).upper()[:34]

        lines = [
            "╔══════════════════════════════════════════════════╗",
            f"║  STRATEGY STATS — {title:<31}║",
            "╠══════════════════════════════════════════════════╣",
            f"║  Date          : {date:<32}║",
            f"║  Last updated  : {clock:<32}║",
            "╠══════════════════════════════════════════════════╣",
            "║  ── TODAY ─────────────────────────────────────  ║",
            f"║  Trades        : {day.trades:<32}║",
            f"║  Wins / Losses : {f'{day.wins} / {day.losses}':<32}║",
            f"║  Win rate      : {day.win_rate():<32}║",
            f"║  P&L           : {d_pnl:<32}║",
            f"║  Volume        : {day.volume:<32,.2f}║",
            f"║  Best streak   : {f'+{day.best_streak}':<32}║",
            f"║  Worst streak  : {day.worst_streak:<32}║",
            "╠══════════════════════════════════════════════════╣",
            "║  ── OVERALL ───────────────────────────────────  ║",
            f"║  Running P&L   : {r_pnl:<32}║",
            f"║  Curr. streak  : {streak:<32}║",
            f"║  Rebuys        : {f'{rebuys} (remaining {rebuys_remaining})':<32}║",
            "╚══════════════════════════════════════════════════╝",
        ]

        try:
            txt_path = os.path.join(strategy_dir, _STATS_TXT_NAME)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass

    def _upsert_history(self, strategy_dir: str, state: _StrategyState) -> None:
        day = state.day
        today_str = day.date or datetime.now().strftime("%Y-%m-%d")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        new_row = {
            "Date": today_str,
            "Trades": str(day.trades),
            "Wins": str(day.wins),
            "Losses": str(day.losses),
            "Win Rate": day.win_rate(),
            "Proft/Loss": str(round(day.profit, 2)),
            "Volume": str(round(day.volume, 2)),
            "Best Streak": str(day.best_streak),
            "Worst Streak": str(day.worst_streak),
            "Running Profit": str(round(state.running_profit, 2)),
            "Last Updated": timestamp,
        }

        history = os.path.join(strategy_dir, _HISTORY_CSV_NAME)
        try:
            rows = []
            if os.path.exists(history):
                with open(history, newline="", encoding="utf-8") as f:
                    rows = [dict(r) for r in csv.DictReader(f)]

            today_seen = False
            updated_rows = []
            for r in rows:
                if r.get("Date") == today_str:
                    if not today_seen:
                        updated_rows.append(new_row)
                        today_seen = True
                    # drop duplicate rows for today silently
                else:
                    updated_rows.append(r)

            if not today_seen:
                updated_rows.append(new_row)

            with open(history, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=_HISTORY_HEADERS, extrasaction="ignore"
                )
                writer.writeheader()
                writer.writerows(updated_rows)

        except OSError:
            pass