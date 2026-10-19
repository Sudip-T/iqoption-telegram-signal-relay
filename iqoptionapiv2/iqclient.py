import os
import time
import logging
import threading
import functools
from dotenv import load_dotenv
from iqoptionapiv2.models import *
from dataclasses import dataclass
from iqoptionapiv2.appstate import appstate
from iqoptionapiv2.trade import TradeManager
from iqoptionapiv2.markets import MarketManager
from iqoptionapiv2.accounts import AccountManager
from iqoptionapiv2._http.auth import IQAuthSession
from typing import Optional, List, Dict, Any, Callable
from iqoptionapiv2.utilities import nested_dict, get_asset_id
from iqoptionapiv2.wsmanager.iqwebsocket import WebSocketManager
from iqoptionapiv2.wsmanager.msg_dispacher import MessageHandler
from iqoptionapiv2.candles import CandleSubscriptionManager


logger = logging.getLogger(__name__)
load_dotenv()


def require_connection(func: Callable) -> Callable:
    """
    Decorator that ensures connection exists before executing the method.
    If not connected, raises ConnectionError or returns appropriate fallback.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs) -> Any:
        if not self.is_connected:
            error_msg = f"Cannot execute {func.__name__} - WebSocket not connected. Call connect() first."
            logger.error(error_msg)
            raise ConnectionError(error_msg)
        return func(self, *args, **kwargs)
    return wrapper

# Optional: Version with auto-reconnect attempt
def require_connection_auto(func: Callable) -> Callable:
    """
    Decorator that attempts automatic reconnection before executing the method.
    Will try to reconnect once, then raise if still not connected.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs) -> Any:
        if not self.is_connected:
            logger.warning(f"Not connected before {func.__name__}, attempting reconnect...")
            if self.reconnect():
                logger.info(f"Reconnected successfully, proceeding with {func.__name__}")
            else:
                error_msg = f"Cannot execute {func.__name__} - Failed to reconnect to IQ Option"
                logger.error(error_msg)
                raise ConnectionError(error_msg)
        return func(self, *args, **kwargs)
    return wrapper

# Alternative: Decorator with custom error handling
def require_connection_with_fallback(fallback_value: Any = None, auto_reconnect: bool = False):
    """
    Decorator factory that provides fallback values or auto-reconnect.
    
    Args:
        fallback_value: Value to return if not connected (default None)
        auto_reconnect: If True, attempt to reconnect before failing
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs) -> Any:
            if not self.is_connected:
                if auto_reconnect:
                    logger.warning(f"Not connected before {func.__name__}, attempting reconnect...")
                    if self.reconnect():
                        logger.info(f"Reconnected successfully, proceeding with {func.__name__}")
                        return func(self, *args, **kwargs)
                
                logger.error(f"Cannot execute {func.__name__} - WebSocket not connected")
                if fallback_value is not None:
                    return fallback_value
                raise ConnectionError(f"WebSocket not connected. Call connect() first.")
            
            return func(self, *args, **kwargs)
        return wrapper
    return decorator

# # Usage examples:
# @require_connection_with_fallback(fallback_value=[])
# def get_candle_history(self, asset_name='EURUSD-op', count=50, timeframe=60):
#     return self.market_manager.get_candle_history(asset_name, count, timeframe)

# @require_connection_with_fallback(fallback_value=0.0)
# def get_balance(self):
#     return self.account_manager.get_active_account_balance()

# @require_connection_with_fallback(fallback_value={"error": "Not connected", "success": False})
# def execute_options_trade(self, trade_params):
#     # ... trade execution code ...


@dataclass
class ReconnectConfig:
    """Configuration for reconnection behavior."""
    max_attempts: int = 3
    base_delay: float = 5.0
    max_delay: float = 30.0
    profile_timeout: float = 15.0
    lock_timeout: float = 10.0


class IQOptionAPIClient:
    """
    Main API class for IQOption automated trading.
    
    Provides a unified interface for account management, market data,
    and trade execution through websocket connections.
    """
    
    def __init__(self, email:str=None, password:str=None, account_type:str='demo',
                 network_check_fn=None):
        """
        Initialize the IQOption API client.
        
        Args:
            email: Login email. Defaults to settings.EMAIL
            password: Login password. Defaults to settings.PASSWORD  
            account_type: # REAL/DEMO/real/demo
            network_check_fn: Optional callable → bool. If provided, _on_websocket_drop
                              will skip self-reconnect when this returns False, deferring
                              recovery entirely to the NetworkMonitor.
        """
        self.email = email or os.getenv('IQ_EMAIL')
        self.password = password or os.getenv('IQ_PASSWORD')
        self.account_mode = account_type
        self.subscribe_candle = []
        self._intentional_disconnect = False
        self._network_check_fn = network_check_fn  # ← injected from main.py
        
        # Configuration
        self.reconnect_cfg = ReconnectConfig()
        self.auth = IQAuthSession(email, password)  # HTTP Authentication Module
        
        # State flags
        self._connected = False
        self._connecting = False
        self._reconnecting = False
        self._lock = threading.RLock()  # Reentrant lock for thread safety
        
        # Initialize core components
        self._init_components()
        
        logger.info('IQOptionAPIClient initialized successfully')
    
    def _init_components(self) -> None:
        """Initialize or re-initialize all core components."""
        self.subscribe_candle = []
        self.candle_generated_check = nested_dict(2, dict)
        
        self.message_handler = MessageHandler()
        self.websocket = WebSocketManager(self.message_handler)
        self.account_manager = AccountManager(
            self.account_mode, self.websocket, self.message_handler
        )
        self.market_manager = MarketManager(self.websocket, self.message_handler)
        self.trade_manager = TradeManager(
            self.websocket, self.message_handler, self.account_manager
        )
        self.websocket.set_drop_callback(self._on_websocket_drop)
        self.candle_manager = CandleSubscriptionManager(self.websocket)
        self.message_handler.set_candle_manager(self.candle_manager)
    
    # ═══════════════════════════════════════════════════════════════════════
    #  Connection Management
    # ═══════════════════════════════════════════════════════════════════════
    def _connect_websocket(self, ssid: str) -> bool:
        """
        Establish WebSocket connection and authenticate.
        
        Args:
            ssid: Session ID for authentication
            
        Returns:
            True if connection and authentication successful
        """
        try:
           
            self.websocket.start_websocket() # Start WebSocket
            self.websocket.send_message('ssid', ssid)# Authenticate
            
            # Wait for profile confirmation
            start_time = time.time()
            while self.message_handler.profile_msg is None:
                if time.time() - start_time > self.reconnect_cfg.profile_timeout:
                    logger.error("Profile timeout — authentication failed")
                    return False
                time.sleep(0.2)
            
            # Set default account
            self.account_manager.set_default_account(self.account_mode)
            self._connected = True
            appstate.update(balance=self.get_balance())
            
            # Start heartbeat thread if not already running
            if not hasattr(self, '_heartbeat_thread') or not self._heartbeat_thread:
                self._heartbeat_thread = threading.Thread(
                    target=self._heartbeat_loop, 
                    name="IQHeartbeat", 
                    daemon=True
                )
                self._heartbeat_thread.start()
            return True
            
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            return False

    def connect(self) -> bool:
        """
        Establish full connection: login + websocket + authentication.
        
        Returns:
            True if connection successful
        """

        if self._connecting:
            logger.warning("⚠️ Connect already in progress!")
            return False
        
        with self._lock:
            self._connecting = True

            try:
                # Step 1: HTTP Login (using auth module)
                ssid = self.auth.get_session_id()
                if not ssid:
                    ssid = self.auth.get_ssid()
                    if not ssid:
                        logger.error("Login failed")
                        return False
            
                # Step 2: WebSocket connection (using existing logic)
                return self._connect_websocket(ssid)
            except Exception as e:
                # logger.error(f"Connection failed: {e}")
                raise  # Re-raise the exception to caller
            finally:
                self._connecting = False
    
    def _calculate_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay."""
        delay = self.reconnect_cfg.base_delay * (2 ** attempt)
        return min(delay, self.reconnect_cfg.max_delay)

    def reconnect(self) -> bool:
        """
        Full reconnection with complete state reset.
        
        Returns:
            True if reconnection successful
        """
        if self._reconnecting:
            print('reconnection already in progress')
            return False  # ← Should return False, not None
        
        self._reconnecting = True
        
        try:
            with self._lock:
                if self.is_connection_alive():
                    return True
                
                logger.info("🔄 Starting IQ Option full reconnection...")
                
                self._close_websocket()# Close existing connection
                self._connected = False# Reset state and components

                self._init_components()
                
                # Attempt reconnection with backoff
                for attempt in range(self.reconnect_cfg.max_attempts):
                    try:
                        if self.connect():
                            logger.info(f"✅ Reconnection successful (attempt {attempt + 1})")
                            return True
                    except Exception as e:
                        logger.error(f"Reconnect attempt {attempt + 1} failed: {e}")
                    
                    # Don't sleep after last attempt
                    if attempt < self.reconnect_cfg.max_attempts - 1:
                        delay = self._calculate_backoff_delay(attempt)
                        logger.info(f"Waiting {delay:.1f}s before next attempt...")
                        time.sleep(delay)
                
                logger.error("❌ Failed to reconnect after all attempts")
                return False
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")
            raise  # Propagate to caller
        finally:
            self._reconnecting = False  # ← ALWAYS reset, even on exception
    
    def _close_websocket(self) -> None:
        """Safely close WebSocket connection."""
        if hasattr(self, 'websocket') and self.websocket:
            try:
                self.websocket.close()
            except Exception as e:
                logger.debug(f"WebSocket close error (ignored): {e}")

    def disconnect(self):
        """Intentional disconnect — suppresses the drop callback."""
        self._intentional_disconnect = True    # ← prevents drop callback firing
        try:
            logger.info("Disconnecting from IQ Option...")
            self._close_websocket()
            try:
                self.auth.logout()
            except Exception as e:
                logger.warning("Error during logout: %s", e)
            self._connected = False
            logger.debug("✅ Disconnected successfully")
        except Exception as e:
            logger.error("Error during disconnect: %s", e)

    @property
    def is_connected(self) -> bool:
        """Returns True only if both the client flag and websocket are live."""
        return (self.websocket is not None
            and self.websocket._is_ws_connected and self._connected
        )

    def is_connection_alive(self) -> bool:
        """
        Check if connection is actually usable.
        Performs lightweight verification without heavy locking.
        """        
        if not self.is_connected:
            return False
        
        # Lightweight verification
        try:
            balance = self.get_balance()
            return balance is not None
        except Exception:
            return False
    
    def ensure_connected(self) -> bool:
        """
        Ensure connection is alive. If not, attempt reconnection.
        Call this BEFORE any trade operation.
        
        Returns:
            True if connection is usable, False otherwise
        """
        
        if self.is_connection_alive():# Fast path - check without lock
            return True
        
        # Slow path - use lock to prevent concurrent reconnections
        with self._lock:
            # Double-check after acquiring lock
            if self.is_connection_alive():
                return True
            
            logger.warning("⚠️ Connection dead — attempting auto-reconnect...")
            return self.reconnect()
    
    def _ensure_connected(self) -> None:
        """Internal version that raises exception on failure."""
        if not self.ensure_connected():
            raise ConnectionError("Failed to establish connection to IQ Option")
    
    def set_network_check(self, fn) -> None:
        """
        Wire in a network-liveness callable after construction.

        Args:
            fn: Zero-argument callable that returns True if internet is up.
                Typically: network_monitor.is_online

        Once set, _on_websocket_drop will skip the self-reconnect cycle
        when the network is known-down and let NetworkMonitor handle recovery.
        """
        self._network_check_fn = fn

    def _on_websocket_drop(self):
        """
        Called by WebSocketManager when the connection drops unexpectedly.

        If a network_check_fn was provided and it returns False (network is
        down), skip the self-reconnect entirely — the NetworkMonitor will
        trigger a clean reconnect once internet is restored.  This avoids
        hammering a dead network with 3 full WebSocket attempts (5s + 10s +
        20s = 35 s of noise) that will all fail anyway.

        If no network_check_fn is provided, or if the network is up, run the
        normal reconnect cycle as before.
        """
        if self._intentional_disconnect or self._reconnecting:
            return

        self._connected = False

        # ── Network-aware early exit ──────────────────────────────────────
        if self._network_check_fn is not None:
            try:
                network_up = self._network_check_fn()
            except Exception:
                network_up = True  # can't tell — assume up and try anyway

            if not network_up:
                logger.info(
                    "🔌 WebSocket dropped but network is DOWN — "
                    "skipping self-reconnect, NetworkMonitor will recover."
                )
                return

        logger.warning("🔌 WebSocket dropped — attempting auto-reconnect...")
        time.sleep(1)  # Small delay to let the connection fully close

        if self.reconnect():
            logger.info("✅ Auto-reconnect successful")
        else:
            logger.error("❌ Auto-reconnect failed after all retries")

    # ═══════════════════════════════════════════════════════════════════════
    #  Heartbeat Management
    # ═══════════════════════════════════════════════════════════════════════

    def _heartbeat_loop(self):
        """
        Sends SSID every 60s to keep session alive.
        If SSID is expired (profile_msg stays None), triggers fresh login.
        """
        while self._connected:
            time.sleep(60)
            
            if not self.is_connected:
                continue # WebSocket drop handler will reconnect

            try:
                if self.is_connection_alive():
                    logger.debug("💓 Heartbeat OK — session alive")
                else:
                    logger.error("Heartbeat NOT OK — CONNECTION UNSTABLE")
                    self.reconnect()

            except Exception as e:
                logger.error("Heartbeat error: %s", e)

    # ═══════════════════════════════════════════════════════════════════════
    #  Account Methods
    # ═══════════════════════════════════════════════════════════════════════
    @require_connection
    def get_balance(self, raw_data=False) -> Optional[float]:
        """Get the balance of the currently active account."""
        return self.account_manager.get_active_account_balance(raw_data)
    
    @require_connection
    def get_tournament_accounts(self) -> List:
        """Retrieve list of available tournament accounts."""
        self.is_connected
        return self.account_manager.get_tournament_accounts()
    
    @require_connection
    def switch_account(self, account_type: str) -> bool:
        """
        Switch to a different account type (demo/real/tournament).
        
        Returns:
            True if switch successful, False if already on target account
        """
        self.is_connected
        
        if account_type.lower() == self.account_manager.current_account_type:
            logger.warning(f'Already on {account_type.lower()} account. No switch needed.')
            return False
        
        return self.account_manager.switch_account(account_type)
    
    # ═══════════════════════════════════════════════════════════════════════
    #  Market Data Methods
    # ═══════════════════════════════════════════════════════════════════════
    @require_connection
    def get_candle_history(self, asset_name: str = 'EURUSD-op', 
                          count: int = 50, timeframe: int = 60) -> List:
        """Retrieve historical candlestick data for an asset."""
        self.is_connected
        return self.market_manager.get_candle_history(asset_name, count, timeframe)
    
    @require_connection
    def save_candles_to_csv(self, candles_data: List = None, 
                           filename: str = 'candles') -> bool:
        """Export candlestick data to CSV file."""
        return self.market_manager.save_candles_to_csv(candles_data, filename)
    
    # ═══════════════════════════════════════════════════════════════════════
    #  Trading Methods
    # ═══════════════════════════════════════════════════════════════════════
    @require_connection
    def execute_options_trade(self, trade_params: OptionsTradeParams) -> Dict[str, Any]:
        """Execute an options trade (digital, binary, or blitz)."""
        # self.is_connected
        
        # Route to appropriate trade manager method
        handlers = {
            OptionType.DIGITAL_OPTION: self.trade_manager._place_digital_option_trade,
            OptionType.BINARY_OPTION: self.trade_manager._place_binary_options_trade,
            OptionType.BLITZ_OPTION: self.trade_manager._place_blitz_options_trade,
        }
        
        handler = handlers.get(trade_params.option_type)
        if not handler:
            raise ValueError(f"Unsupported option type: {trade_params.option_type}")
        
        return handler(trade_params)
    
    @require_connection
    def get_trade_outcome(self, order_id: int, expiry: int = 1) -> Dict:
        """Get the outcome of a completed trade."""
        # self.is_connected
        return self.trade_manager.get_trade_outcome(order_id, expiry=expiry)

    @require_connection
    def subscribe_realtime_candles(self, asset: str, candle_size: int) -> bool:

        subscription_key = f"{asset},{candle_size}"

        # Avoid duplicate subscriptions
        if subscription_key not in self.subscribe_candle:
            self.subscribe_candle.append(subscription_key)

        start_time = time.time()
        # Reset candle confirmation flag
        self.message_handler.candle_generated_check[str(asset)][int(candle_size)] = False

        # Websocket subscription message
        subscribe_payload = {
            "name": "candle-generated",
            "params": {
                "routingFilters": {
                    "active_id": get_asset_id(asset),
                    "size": candle_size
                }
            }
        }

        # Send subscription request
        self.websocket.send_message("subscribeMessage", subscribe_payload)

        # Wait until first candle arrives (confirmation)
        while True:

            # Timeout protection
            if time.time() - start_time > 20:
                logging.error(
                    "Real-time candle subscription timeout (20s) "
                    f"for asset={asset}, size={candle_size}"
                )
                return False

            try:
                # MessageHandler sets this to True when first candle arrives
                if self.message_handler.candle_generated_check[str(asset)][int(candle_size)]:

                    logging.debug(
                        f"Realtime candle stream active → {asset} ({candle_size}s)"
                    )
                    return True
            except Exception as e:
                print(e)

            time.sleep(1)

    def unsubscribe(self, asset, size):
        
        data = {"name":"candle-generated",
                "params":{
                    "routingFilters":{
                                        "active_id":str(asset),
                                        "size":int(size)
                                        }
                        }
                }

        self.websocket.send_message("unsubscribeMessage", data)

    def stop_candles_one_stream(self, ACTIVE, size):
        if ((ACTIVE + "," + str(size)) in self.subscribe_candle) == True:
            self.subscribe_candle.remove(ACTIVE + "," + str(size))
        while True:
            try:
                if self.message_handler.candle_generated_check[str(ACTIVE)][int(size)] == {}:
                    return True
            except:
                pass
            self.message_handler.candle_generated_check[str(ACTIVE)][int(size)] = {}
            self.unsubscribe(get_asset_id(ACTIVE), size)
            time.sleep(.5)

    def get_current_price(self, asset: str) -> Optional[float]:
        """
        Get current price from real-time candle stream.
        """
        try:
            # Use asset directly (don't remove suffix)
            # because your real_time_candles stores with suffix
            candles_by_timeframe = self.message_handler.real_time_candles.get(asset, {})
            
            if not candles_by_timeframe:
                return None
            
            # Get 60-second candles
            candles = candles_by_timeframe.get(60, {})
            
            if not candles:
                return None
            
            # Get most recent candle
            latest_timestamp = max(candles.keys())
            latest_candle = candles[latest_timestamp]
            
            # Return close price
            return float(latest_candle.get('close', 0))
            
        except Exception as e:
            logger.debug(f"get_current_price error for {asset}: {e}")
            return None


    def on_new_candle(self, callback: Callable):
        """
        Register callback for when a new candle completes.
        
        Args:
            callback: Function that accepts a Candle object
            
        Example:
            def my_callback(candle):
                print(f"New candle: {candle.asset_name} close: {candle.close}")
            
            client.on_new_candle(my_callback)
        """
        self.candle_manager.on_new_candle(callback)
        
    @require_connection 
    def start_candle_stream(self, asset: str, candle_size: int = 60) -> bool:
        return self.candle_manager.subscribe(asset, candle_size)






    def get_all_open_time(self):
        # all pairs openned
        self.OPEN_TIME = nested_dict(3, dict)
        binary = threading.Thread(target=self.__get_binary_open)
        binary.start()
        binary.join()

        return self.OPEN_TIME
    

    def __get_binary_open(self):
        # for turbo and binary pairs
        binary_data = self.get_all_init_v2()
        binary_list = ["binary", "turbo"]
        if binary_data:
            for option in binary_list:
                if option in binary_data:
                    for actives_id in binary_data[option]["actives"]:
                        active = binary_data[option]["actives"][actives_id]
                        name = str(active["name"]).split(".")[1]
                        if active["enabled"] == True:
                            if active["is_suspended"] == True:
                                # print(active)
                                self.OPEN_TIME[option][name]["open"] = False
                            else:
                                self.OPEN_TIME[option][name]["open"] = True
                        else:
                            self.OPEN_TIME[option][name]["open"] = active["enabled"] 

    def get_all_init_v2(self):
        self.message_handler.api_option_init_all_result_v2 = None

        self.get_api_option_init_all_v2()
        start_t = time.time()
        while self.message_handler.api_option_init_all_result_v2 == None:
            if time.time() - start_t >= 30:
                logging.error('**warning** get_all_init_v2 late 30 sec')
                return None
        return self.message_handler.api_option_init_all_result_v2
    
    def get_api_option_init_all_v2(self):

        msg = {"name": "get-initialization-data",
               "version": "3.0",
               "body": {}
               }
        self.websocket.send_message(name="sendMessage", msg=msg)


    def subscribe_live_candles(self, actives) -> bool:
        """Subscribe to all active actives and register the candle callback."""

        results: dict[str, bool] = {}
        threads = []

        def subscribe_one(active):
            results[active.asset] = self.candle_manager.subscribe(
                active.candle_asset,
                timeframe=active.candle_timeframe,
            )

        for i, active in enumerate(actives):
            # Add delay before each subscription (except first)
            if i > 0:
                time.sleep(0.5)  # 200ms delay

            t = threading.Thread(target=subscribe_one, args=(active,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # failed = [a for a, ok in results.items() if not ok]
        # success = [a for a, ok in results.items() if ok]

        failed, success = [], []
        for asset, ok in results.items():
            (success if ok else failed).append(asset)

        if failed:
            logger.error("Failed to subscribe: %s", failed)

        # self.on_new_candle(self.on_new_candle)
        self._running = True
        return len(success) > 0