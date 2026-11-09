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