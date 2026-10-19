import time
import logging
import concurrent.futures
from dataclasses import dataclass
from collections import defaultdict, deque
from typing import Optional, List, Callable
from iqoptionapiv2.instruments.options_assests import UNDERLYING_ASSESTS

logger = logging.getLogger(__name__)


@dataclass
class Candle:
    """Clean candle data structure."""
    asset_name: str
    asset_id: int
    timeframe: int
    timestamp: int
    open: float
    close: float
    high: float
    low: float
    volume: float

    @classmethod
    def from_iq_message(cls, asset_name: str, asset_id: int, msg: dict) -> 'Candle':
        return cls(
            asset_name=asset_name,
            asset_id=asset_id,
            timeframe=msg.get('size'),
            timestamp=msg.get('from'),
            open=msg.get('open'),
            close=msg.get('close'),
            high=msg.get('max'),
            low=msg.get('min'),
            volume=msg.get('volume', 0)
        )

    def __getitem__(self, key):
        # maps old dict keys to attributes ('max'/'min' → high/low, 'from' → timestamp)
        aliases = {'max': 'high', 'min': 'low', 'from': 'timestamp', 'size': 'timeframe'}
        return getattr(self, aliases.get(key, key))

class CandleSubscriptionManager:
    """
    COMPLETE OWNER of all candle-related operations.
    """

    def __init__(self, websocket):
        """Initialize with WebSocket connection."""
        self.websocket = websocket

        # FIXED: Build maps correctly from UNDERLYING_ASSESTS
        # UNDERLYING_ASSESTS format: {"EURUSD": 1, "GBPUSD": 2, ...}
        self.name_to_id = UNDERLYING_ASSESTS  # "EURUSD" → 1
        self.id_to_name = {v: k for k, v in UNDERLYING_ASSESTS.items()}  # 1 → "EURUSD"

        # ─── Candle Storage ───
        self._candles: dict[str, dict[int, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=110))
        )

        # Live/current candle (being built)
        self._current_candle: dict[str, dict[int, Optional[dict]]] = defaultdict(
            lambda: defaultdict(lambda: None)
        )

        # ─── Subscription Management ───
        self._active_subscriptions: set[str] = set()  # {"EURUSD,60"}
        self._subscription_confirmed: dict[str, bool] = {}  # FIXED: Regular dict, not defaultdict

        # ─── Callbacks ───
        self._new_candle_callbacks: List[Callable] = []
        self._live_candle_callbacks: List[Callable] = []

        # Shared pool for on_new_candle_async() — created lazily so it's
        # only spun up if something actually registers an async callback.
        self._async_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None

    # -----------------------------------------------------------------
    # Entry point - called by MessageHandler
    # -----------------------------------------------------------------
    def set_max_dicts(self, min_candles):
        self._candles: dict[str, dict[int, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=min_candles))
        )

    def on_candle_message(self, message: dict) -> None:
        """
        Called by MessageHandler._handle_candles_generated().
        This is the ONLY connection between MessageHandler and candles.
        """
        msg = message.get('msg', {})
        asset_id = msg.get('active_id')
        timeframe = msg.get('size')

        if not asset_id or not timeframe:
            logger.warning(f"Missing asset_id or timeframe: {msg}")
            return

        # FIXED: Get asset name from ID using id_to_name
        asset_name = self.id_to_name.get(asset_id)
        if not asset_name:
            logger.warning(f"Unknown asset_id: {asset_id}")
            return

        # Process the candle
        self._process_candle(asset_name, asset_id, timeframe, msg)

    def _process_candle(self, asset_name: str, asset_id: int, timeframe: int, msg: dict):
        """Process incoming candle - detects new vs updated candle."""
        candle_id = msg.get('id')
        timestamp = msg.get('from')

        # Check if this is a new candle or update to current
        current = self._current_candle[asset_name][timeframe]

        if current is None or current.get('id') != candle_id:
            # NEW CANDLE - previous candle is now closed
            if current is not None:
                # Store the completed candle in history
                completed_candle = Candle.from_iq_message(asset_name, asset_id, current)
                self._candles[asset_name][timeframe].append(completed_candle)

                # Snapshot AFTER appending, so it includes the just-closed candle
                history = list(self._candles[asset_name][timeframe])

                # logger.debug(f"✅ New candle closed: {asset_name} {timeframe}s @ {completed_candle.timestamp}")

                # Notify new candle callbacks
                for cb in self._new_candle_callbacks:
                    try:
                        # cb(completed_candle)
                        cb(completed_candle, history)
                    except Exception as e:
                        logger.error(f"New candle callback failed: {e}")

            # Start tracking new current candle
            self._current_candle[asset_name][timeframe] = msg
        else:
            # UPDATE to current candle (live price changes)
            self._current_candle[asset_name][timeframe] = msg

            # Notify live update callbacks
            for cb in self._live_candle_callbacks:
                try:
                    cb(asset_name, timeframe, msg)
                except Exception as e:
                    logger.error(f"Live candle callback failed: {e}")

        # FIXED: Mark subscription as confirmed for subscribe() wait loop
        # sub_key = f"{asset_name}_{timeframe}"
        # if not self._subscription_confirmed[sub_key] and sub_key in self._active_subscriptions:
        #     self._subscription_confirmed[sub_key] = True
        #     logger.debug(f"✅ Subscription confirmed: {sub_key}")

        sub_key = f"{asset_name}_{timeframe}"
        if sub_key in self._active_subscriptions and not self._subscription_confirmed.get(sub_key, False):
            self._subscription_confirmed[sub_key] = True
            logger.debug(f"✅ Subscription confirmed: {sub_key}")

    # -----------------------------------------------------------------
    # Subscription Methods
    # -----------------------------------------------------------------

    def subscribe(self, asset: str, timeframe: int = 60, timeout: int = 20) -> bool:
        """Subscribe to real-time candles."""
        if not self.websocket:
            logger.error("WebSocket not set")
            return False

        sub_key = f"{asset}_{timeframe}"

        # Already subscribed?
        if sub_key in self._active_subscriptions:
            logger.info(f"Already subscribed to {sub_key}")
            return True

        # FIXED: Get asset ID from name_to_id
        asset_id = self.name_to_id.get(asset)
        if not asset_id:
            logger.error(f"Unknown asset: {asset}")
            return False

        # Add to active subscriptions BEFORE waiting for confirmation
        self._active_subscriptions.add(sub_key)

        # Reset confirmation flag for this subscription
        self._subscription_confirmed[sub_key] = False

        # Build subscription payload
        payload = {
            "name": "candle-generated",
            "params": {
                "routingFilters": {
                    "active_id": asset_id,
                    "size": timeframe
                }
            }
        }

        logger.info(f"🔌 Subscribing to {asset} (ID:{asset_id}) {timeframe}s candles...")

        # Send subscription request
        self.websocket.send_message("subscribeMessage", payload)

        # Wait for confirmation (first candle)
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._subscription_confirmed.get(sub_key, False):
                logger.info(f"✅ Subscribed to {asset} {timeframe}s candles")
                return True
            time.sleep(0.5)

        # Timeout - remove from active subscriptions
        self._active_subscriptions.discard(sub_key)
        self._subscription_confirmed.pop(sub_key, None)

        logger.error(f"❌ Subscription timeout for {asset} {timeframe}s (waited {timeout}s)")
        return False

    def unsubscribe(self, asset: str, timeframe: int) -> bool:
        """Unsubscribe from real-time candles."""
        if not self.websocket:
            return False

        sub_key = f"{asset},{timeframe}"

        if sub_key not in self._active_subscriptions:
            logger.debug(f"Not subscribed to {sub_key}")
            return True

        asset_id = self.name_to_id.get(asset)
        if not asset_id:
            logger.error(f"Unknown asset: {asset}")
            return False

        payload = {
            "name": "candle-generated",
            "params": {
                "routingFilters": {
                    "active_id": asset_id,
                    "size": timeframe
                }
            }
        }

        logger.info(f"🔌 Unsubscribing from {asset} {timeframe}s candles...")
        self.websocket.send_message("unsubscribeMessage", payload)

        self._active_subscriptions.discard(sub_key)
        self._subscription_confirmed.pop(sub_key, None)

        return True

    def unsubscribe_all(self):
        """Unsubscribe from all active streams."""
        for sub_key in list(self._active_subscriptions):
            asset, size = sub_key.split(',')
            self.unsubscribe(asset, int(size))

    # -----------------------------------------------------------------
    # Data Access Methods
    # -----------------------------------------------------------------

    def get_candles(self, asset: str, timeframe: int, count: int = 10) -> List[Candle]:
        """Get last N completed candles."""
        dq = self._candles[asset][timeframe]
        if not dq:
            return []
        return list(dq)[-count:]

    def get_latest_candle(self, asset: str, timeframe: int) -> Optional[Candle]:
        """Get most recent COMPLETED candle."""
        dq = self._candles[asset][timeframe]
        return dq[-1] if dq else None

    def get_current_candle(self, asset: str, timeframe: int) -> Optional[dict]:
        """Get the LIVE (in-progress) candle."""
        return self._current_candle[asset][timeframe]

    def get_current_price(self, asset: str, timeframe: int = 60) -> Optional[float]:
        """Get current price (close of latest candle OR live price)."""
        current = self.get_current_candle(asset, timeframe)
        if current:
            return current.get('close')

        latest = self.get_latest_candle(asset, timeframe)
        return latest.close if latest else None

    def get_candle_count(self, asset: str, timeframe: int) -> int:
        """Get number of stored candles."""
        return len(self._candles[asset][timeframe])

    def is_subscribed(self, asset: str, timeframe: int) -> bool:
        """Check if actively subscribed."""
        return f"{asset},{timeframe}" in self._active_subscriptions

    # -----------------------------------------------------------------
    # Callbacks
    # -----------------------------------------------------------------

    def on_new_candle(self, callback: Callable[[Candle], None]) -> None:
        """Register callback for every NEW completed candle."""
        self._new_candle_callbacks.append(callback)

    def on_live_candle_update(self, callback: Callable) -> None:
        """Register callback for live candle price updates."""
        self._live_candle_callbacks.append(callback)

    # -----------------------------------------------------------------
    # Debug Helpers
    # -----------------------------------------------------------------

    def get_status(self) -> dict:
        """Get current status for debugging."""
        return {
            "active_subscriptions": list(self._active_subscriptions),
            "confirmed": dict(self._subscription_confirmed),
            "candle_counts": {
                f"{asset},{tf}": len(self._candles[asset][tf])
                for asset in self._candles
                for tf in self._candles[asset]
            }
        }


    def on_new_candle_async(self, callback: Callable[['Candle', list], None], max_workers: int = 4) -> None:
        """
        Register callback that runs in a shared thread pool instead of on the
        WebSocket thread, so a slow/blocking callback for one asset can't
        delay candle processing for every other asset.

        FIXED (was broken): the wrapped callback previously took only
        `candle` and dropped `history` entirely, but `_process_candle`
        always invokes callbacks as `cb(completed_candle, history)` — the
        old version would raise a TypeError the first time a candle closed.
        It also created a brand-new ThreadPoolExecutor on every call to
        this method; now it's built once and shared across every async
        registration.

        Only use this for callbacks that are safe to run out of order /
        concurrently with each other (e.g. a callback that just computes a
        signal and hands it to a queue). If your callback needs to see
        candles for the SAME asset strictly in arrival order, don't rely on
        this — thread pool submission order isn't guaranteed to finish in
        order.
        """
        if self._async_executor is None:
            self._async_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="CandleAsync"
            )

        executor = self._async_executor

        def wrapped_callback(candle, history):
            future = executor.submit(callback, candle, history)

            def _log_if_failed(f):
                exc = f.exception()
                if exc is not None:
                    logger.error(f"Async new-candle callback failed: {exc}")

            future.add_done_callback(_log_if_failed)

        self._new_candle_callbacks.append(wrapped_callback)