import os
import sys
import time
import logging
import argparse
import threading
import signal as signal_module

# ── Path setup ────────────────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    sys.path.insert(0, sys._MEIPASS)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.environ.setdefault('ENV_FILE', os.path.join(BASE_DIR, '.env'))

# ── Logging setup ─────────────────────────────────────────────────────────────

class TruncateFilter(logging.Filter):
    def filter(self, record):
        record.name = record.name[:15]
        return True

_formatter = logging.Formatter(
    fmt='%(asctime)s %(name)-8s %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(_formatter)

_file_handler = logging.FileHandler(
    "system.log",
    mode='w',
    encoding="utf-8",
)

_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_formatter)

logging.basicConfig(level=logging.DEBUG, handlers=[_console, _file_handler])
for _h in logging.root.handlers:
    _h.addFilter(TruncateFilter())

log = logging.getLogger("MT4IQ Signal Bridge Trader")

from iqoptionapiv2.models import Direction
from settings import Config
from iengine.network_monitor import NetworkMonitor
from iengine._iq_engine import IQOptionEngine

try:
    from iengine._errors import register_iq_option_error_handlers
    register_iq_option_error_handlers()
except ImportError:
    error_handler = None

# ── Config singleton ──────────────────────────────────────────────────────────
config = Config()


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Application
# ═══════════════════════════════════════════════════════════════════════════════

class IQTournamentTrader:
    """
    Connects immediately on start, runs until Ctrl+C or SIGTERM.
    No scheduling windows — always on.
    """

    def __init__(self):
        self.config                            = config

        self._poll_thread: threading.Thread    = None
        self._running                          = False
        self._shutdown_event                   = threading.Event()
        self._start_time: float                = None

        self.network_monitor: NetworkMonitor   = None
        self.executor: IQOptionEngine      = IQOptionEngine(self.config)

    # ── Startup ───────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Connect IQ Option and start the bridge reader. Returns False on failure."""

        # Start IQ poll loop in background thread
        self._poll_thread = threading.Thread(
            target=self._run_poll_loop,
            name="IQPollLoop",
            daemon=True,
        )
        self._poll_thread.start()

        # Wait up to 30 s for IQ Option to connect
        for _ in range(30):
            if self.executor.client and self.executor.client.is_connected:
                time.sleep(1)
                break
            time.sleep(1)
        else:
            log.error("❌ IQ Option failed to connect within 30 seconds")
            return False

        self._start_time = time.time()
        print("")
        return True

    # ── Run loop ──────────────────────────────────────────────────────────────

    def run(self):
        self._running = True

        # Network monitor handles reconnection automatically
        self.network_monitor = NetworkMonitor(
            check_interval      = 30.0,
            recovery_callback   = self._on_network_recovery,
            disconnect_callback = self._on_network_disconnect,
            reconnect_callbacks = {
                "iqoption": self._reconnect_iq,
            },
        )
        self.network_monitor.start()
        # self.executor.client.on_new_candle(self.TASignals)

        try:
            while self._running:
                time.sleep(60)
        except KeyboardInterrupt:
            self.shutdown()

    def _run_poll_loop(self):
        try:
            self.executor.start()
        except Exception as e:
            log.error("Poll loop crashed: %s", e, exc_info=True)

    # ── Network callbacks ─────────────────────────────────────────────────────

    def _on_network_disconnect(self):
        log.warning("⚠️ Internet lost — will reconnect automatically when restored")
        if self.executor:
            self.executor.pause()

    def _on_network_recovery(self):
        log.info("✅ Internet restored")
        if self.executor:
            self.executor.resume()

    def _reconnect_iq(self):
        log.info("🔄 Reconnecting IQ Option…")
        self.executor.reconnect()
        success = self.executor.client.subscribe_live_candles(self.config.active_pairs)
        if success:
            self.executor.client.on_new_candle(self.engine.on_new_candle)
        else:
            sys.exit(0)

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def shutdown(self, signum=None, frame=None):
        if not self._running:
            return

        log.info("Shutting down…")
        self._running = False
        self._shutdown_event.set()

        if self.executor:
            self.executor.stop()
            try:
                self.executor.client.disconnect()
            except Exception:
                pass

        if self.network_monitor:
            self.network_monitor.stop()

        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)

        # self._log_session_summary()
        log.info("Shutdown complete")
        sys.exit(0)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log_session_summary(self):
        if not self._start_time:
            return
        elapsed = time.time() - self._start_time
        minutes, seconds = int(elapsed // 60), int(elapsed % 60)
        stats = self.executor.stats
        log.info("─" * 50)
        log.info("  SESSION SUMMARY")
        log.info("  Duration   : %dm %ds", minutes, seconds)
        log.info("  Signals    : %d", stats.signal_received)
        log.info("  Trades     : %d", stats.trades_today)
        log.info("  P&L        : %+.2f", stats.profit_today)
        log.info("  Balance    : %.2f", stats.balance)
        log.info("─" * 50)





# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="MT4IQ Signal Bridge Trader")
    parser.add_argument("--account",  default=None, choices=["PRACTICE", "REAL"],
                        help="Override .env account type")
    parser.add_argument("--disabled", action="store_true",
                        help="Dry-run: signals received but no trades placed")
    parser.add_argument("--reverse",  action="store_true",
                        help="Reverse all 60S signals (CALL→PUT, PUT→CALL)")
    args = parser.parse_args()

    if args.account:
        config.IQ_ACCOUNT_TYPE = args.account.lower()

    if args.reverse:
        log.warning("⚠️  --reverse flag active: all 60S signals will be inverted")

    trader = IQTournamentTrader()

    signal_module.signal(signal_module.SIGINT,  trader.shutdown)
    signal_module.signal(signal_module.SIGTERM, trader.shutdown)

    if not trader.start():
        logging.error("Failed to start — exiting")
        # sys.exit(1)

    trader.run()


if __name__ == "__main__":
    main()