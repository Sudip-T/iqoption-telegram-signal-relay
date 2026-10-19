"""
IQ Option Executor that reads from shared queue
═══════════════════════════════════════════════════════════════════════════
Reads signals from trade_queue.py every 300ms and executes trades directly.
No MT4, no JSON bridge file — just queue + direct execution.
"""

import time
import logging
import threading
from settings import Config
from iqoptionapiv2.iqclient import IQOptionAPIClient
from iqoptionapiv2.appstate import appstate, TournamentAccount
# from ssv2_engine._stats_reporter import StatsReporter, DayTotals
from dataclasses import dataclass
from typing import Optional


log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
#  Main Loop (Poll Queue Every 300ms)
# ═══════════════════════════════════════════════════════════════════════

class IQOptionEngine:
    def __init__(self, cfg:Config):
        self.cfg = cfg
        self.monitor = None
        self._running = True
        self._stats = {"trades": 0, "profit": 0.0}
        # self.stats = SessionStats()
        self._stats_lock = threading.Lock()
        self._lock = threading.Lock()
        self._is_reconnecting = False
        self.max_retries = 2
        self.client = IQOptionAPIClient(
            self.cfg.IQ_EMAIL, self.cfg.IQ_PASSWORD, self.cfg.IQ_ACCOUNT_TYPE)
        # self._reporter = StatsReporter()  # File-based stats (TXT + CSV)
        # self.day = DayTotals()            # Day-wide accumulator (across sessions)

    def connect(self) -> bool:
        """Establish connection with retry logic"""
        with self._lock:
                    
            for attempt in range(self.max_retries):
                try:
                    log.info("Connecting to IQ Option (attempt %d/%d)", 
                                attempt + 1, self.max_retries)
                    self.client.connect()
                    
                    # Verify connection
                    balance = self.client.get_balance()
                    if balance is not None:
                        log.info("IQ Option connected successfully")
                        return True
                        
                except Exception as e:
                    log.error("Connection attempt %d failed: %s", attempt + 1, e)
                    if attempt < self.max_retries - 1:
                        time.sleep(5 * (attempt + 1))  # Exponential backoff
                        
            log.error("Failed to connect to IQ Option after %d attempts", self.max_retries)
            return False

    def reconnect(self) -> bool:
        """
        Reconnect to IQ Option with proper state management.
        Thread-safe wrapper around client's reconnect.
        """
        
        # Thread safety lock
        if not hasattr(self, '_reconnect_lock'):
            self._reconnect_lock = threading.Lock()
        
        # Non-blocking lock acquisition
        if not self._reconnect_lock.acquire(blocking=False):
            log.warning("Reconnection already in progress (locked)")
            return False
        
        try:
            log.info("Attempting to reconnect to IQ Option...")
            
            # Option A: Use client's built-in reconnect (if available)
            if hasattr(self.client, 'reconnect'):
                log.debug("Using client's built-in reconnect method")
                success = self.client.reconnect()
                
                if success:
                    # Update executor's stats
                    balance = self.client.get_balance()
                    if balance is not None:
                        log.info("IQ Option connected successfully")
                    return True
                else:
                    log.error("Client reconnect failed")
                    return False
            
        except Exception as e:
            log.error(f"Reconnection error: {e}")
            return False
            
        finally:
            # Always release the lock
            self._reconnect_lock.release()
            log.debug("Reconnection lock released")

    def start(self):
        if self.connect():

            # Main polling loop
            while self._running:
                try:
                    time.sleep(5)    
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    log.error(f"Poll loop error: {e}")
                
                time.sleep(0.5)


    def _print_status(self):
        """Write current stats to daily_stats.txt and trading_history.csv."""
        self._reporter.update(self.stats)

    def get_current_price(self, asset: str):
        """
        Fetch the current mid-price for an asset from IQ Option.
        Returns None if the price cannot be fetched (used by BetterPriceWatcher).
        """
        try:
            price = self.client.get_current_price(asset)
            return float(price) if price is not None else None
        except Exception as e:
            log.debug("Price fetch error for %s: %s", asset, e)
            return None
    
    def pause(self):
        """
        Pause the poll loop status prints and trade result monitor.
        Called when network goes down. Signals already in-flight are
        preserved — the monitor will check them once resumed.
        """
        self._paused = True
        log.info("⏸️  IQOptionEngine paused")

    def resume(self):
        """
        Resume normal operation after network restoration.
        """
        self._paused = False
        # Reset status timer so a fresh status line prints shortly after resume
        self._last_status_time = 0
        log.info("▶️  IQOptionEngine resumed")

    def stop(self):
        self._running = False