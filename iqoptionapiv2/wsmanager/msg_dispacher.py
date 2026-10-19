import json
import logging
from iqoptionapiv2.appstate import appstate
from iqoptionapiv2.utilities import nested_dict
from iqoptionapiv2.models import TradeOutcomeChecker
from iqoptionapiv2.candles import CandleSubscriptionManager
from iqoptionapiv2.instruments.options_assests import UNDERLYING_ASSESTS
from telegram_notifier import TelegramNotifier
from telegram_notifier import TelegramNotifier
logger = logging.getLogger(__name__)


class MessageHandler:
    """
    Handles various types of messages received from IQ Option Websocket.
    """
    def __init__(self, candle_manager:CandleSubscriptionManager=None):
        """
        Initialize the MessageHandler with default values for all message types.
        
        Sets up storage for profile data, balance information, market data, and position tracking.
        """
        self.server_time = None

        self.candle_manager = candle_manager

        # User profile and account information
        self.profile_msg = None
        self.balance_data = None

        # Market and time data
        self.candles = None
        self.underlying_list = None
        self.initialization_data = None
        self._underlying_assests = None

        # Position tracking
        self.hisory_positions = None

        self.position_info = {}
        self.orders_confirmation = {}

        self.real_time_candles = nested_dict(3, dict)
        self.candle_generated_check = nested_dict(2, dict)
        self.real_time_candles_maxdict_table = nested_dict(2, dict)

        self.trade_outcome_checker = TradeOutcomeChecker()

        self.asset_id_map = {v: k for k, v in UNDERLYING_ASSESTS.items()}

        self.api_option_init_all_result_v2 = []

        self.telegram_notifier = TelegramNotifier()

    def dict_queue_add(
        self,
        dict_obj: dict,
        maxdict: int,
        key1,
        key2,
        key3,
        value
    ) -> None:
        """
        Insert a value into a nested dictionary while enforcing a maximum size.

        This function maintains a rolling buffer of values for each
        asset/timeframe pair. If the number of stored items exceeds the
        allowed limit (`maxdict`), the oldest entry is removed automatically.

        Structure
        ---------
        dict_obj[key1][key2][key3] = value

        Example:
        --------
        real_time_candles = {
            "EURUSD": {
                60: {
                    1710000000: {...},
                    1710000060: {...}
                }
            }
        }

        Parameters
        ----------
        dict_obj : dict
            The nested dictionary to update.

        maxdict : int
            Maximum number of entries allowed for each (key1, key2) group.

        key1 : Any
            First-level key (typically asset symbol).

        key2 : Any
            Second-level key (typically candle timeframe).

        key3 : int
            Third-level key (typically timestamp).

        value : Any
            Value to store (usually candle data).

        Notes
        -----
        - Oldest entry is determined by the smallest timestamp key.
        - Prevents uncontrolled memory growth in long-running bots.
        - Efficient for streaming market data.
        """

        # Ensure first-level key exists
        if key1 not in dict_obj:
            dict_obj[key1] = {}

        # Ensure second-level key exists
        if key2 not in dict_obj[key1]:
            dict_obj[key1][key2] = {}

        # If this timestamp is new, enforce max size
        if key3 not in dict_obj[key1][key2]:

            # Check if buffer limit is reached
            if len(dict_obj[key1][key2]) >= maxdict:

                # Remove oldest candle (smallest timestamp)
                oldest_key = min(dict_obj[key1][key2].keys())
                del dict_obj[key1][key2][oldest_key]

        # Store the new candle data
        dict_obj[key1][key2][key3] = value


    def set_candle_manager(self, candle_manager):
        """Inject candle manager after initialization."""
        self.candle_manager = candle_manager

    def handle_message(self, message):
        """
        Route incoming messages to appropriate handler method based on message name.
        
        Args:
            message (dict): The incoming message containing 'name' and other data
        """
        message_name = message.get('name')

        # Map message names to their corresponding handler methods
        handlers = {
            'profile': self._handle_profile,
            'candles': self._handle_candles,
            'balances': self._handle_balances,
            'timeSync': self._handle_server_time,
            'underlying-list': self._handle_underlying_list,
            'initialization-data': self._handle_initialization_data,
            'training-balance-reset': self._handle_training_balance_reset,
            "history-positions":self._handle_position_history,
            "digital-option-placed":self._handle_option_opened,
            "option-opened":self._handle_option_opened,
            "position-changed":self._handle_position_changed,
            "option":self._handle_option_opened,

            "socket-option-closed":self._handle_socket_option_closed,
            "candle-generated":self._handle_candles_generated,
        }

        # Get the appropriate handler and invoke it if found
        handler = handlers.get(message_name)
        if handler:
            handler(message)
    
    def _handle_server_time(self, message):
        """
        Handle server time synchronization messages.
        
        Args:
            message (dict): Message containing server time information in 'msg' field
        """
        self.server_time = message['msg']
        # print('timestamp: ',message['msg']/1000)
    
    def _handle_profile(self, message):
        """
        Handle user profile messages and extract active balance ID for demo account.
        
        Processes profile data and identifies the demo account baalance ID (type 4).
        Real account balance has type 1, demo account has type 4.
        
        Args:
            message (dict): Profile message containing user account information and balances
        """
        self.profile_msg = message
        balances = message['msg']['balances']

        # Find demo account balance (type 4) and set as active
        for balance in balances:
            if balance['type'] == 4:  # Demo account | 1 for real, 4 for demo
                self.active_balance_id = balance['id']
                appstate.update(balance=balance['amount'])
                break
    
    def _handle_balances(self, message):
        """
        Handle balance update messages.
        
        Args:
            message (dict): Message containing current balance information
        """
        
        self.balance_data = message['msg']
    
    def _handle_training_balance_reset(self, message):
        """
        Handle demo account balance reset responses.
        
        Logs the result of balance reset operations with appropriate log levels
        based on the status code received.
        
        Args:
            message (dict): Response message from balance reset request
                          Contains 'status' field and optional error message
        """
        if message['status'] == 2000: # Success status code
            logger.info('Demo Acoount Balance Reset Successfully')
        elif message['status'] == 4001: # Error status code
            logger.warning(message['msg']['message'])
        else:
            logger.info(message)
    
    def _handle_initialization_data(self, message):
        """
        Handle platform initialization data.
        
        Args:
            message (dict): Initialization message containing underlying assets and platform data
        """
        # print('message', message)
        self._underlying_assests = message['msg']
        self.api_option_init_all_result_v2 = message["msg"]
    
    def _handle_candles(self, message):
        """
        Handle candlestick/OHLCV price data messages.
        
        Args:
            message (dict): Message containing candle data in 'msg.candles' field
        """
        self.candles = message['msg']['candles']
    
    def _handle_underlying_list(self, message):
        """
        Handle underlying asset list messages.
        
        Processes different types of underlying asset lists based on the message type.
        Digital options and other instrument types may have different data structures.
        
        Args:
            message (dict): Message containing underlying asset information
        """
        if message['msg'].get('type', None) == 'digital-option':
            # Digital options have underlying assets in 'underlying' field
            self._underlying_assests = message['msg']['underlying']
        else:
            # marginal instrument types have assets in 'items' field
            self._underlying_assests = message['msg']['items']

    def _save_data(self, message, filename):
        """
        Utility method to save message data to a JSON file.
        
        Args:
            message (dict): The message data to save
            filename (str): The filename (without .json extension) to save to
        """
        with open(f'{filename}.json', 'w') as file:
            json.dump(message, file, indent=4)
        
    def _handle_position_history(self, message):
        """
        Handle historical position data messages.
        
        Args:
            message (dict): Message containing historical position data in 'msg.positions' field
        """
        self.hisory_positions = message['msg']['positions']

    # def _handle_option_opened(self, message):
    #     """
    #     Handle digital option placement confirmation messages.
    #     Formats and posts a trade signal to Telegram.
    #     """
    #     msg = message['msg']

    #     expiration_time = msg['expiration_time']
    #     open_time_ms = msg['open_time_millisecond']
    #     open_time_seconds = open_time_ms / 1000

    #     duration_seconds = expiration_time - open_time_seconds
    #     duration_minutes = duration_seconds / 60

    #     signal_text = (
    #         f"🚨 <b>New Signal</b>\n"
    #         f"Option ID: <code>{msg['option_id']}</code>\n"
    #         f"Asset: <b>{msg['active']}</b>\n"
    #         f"Direction: <b>{msg['direction'].upper()}</b>\n"
    #         f"Duration: {duration_seconds:.0f}s ({duration_minutes:.2f} min)"
    #     )

    #     logger.info(signal_text.replace('\n', ' | '))  # keep local logging too

    #     if self.telegram_notifier:
    #         self.telegram_notifier.send(signal_text)

    def _handle_position_changed(self, message):
        """
        Handle position status change messages.
        
        Updates position information and saves the latest position data to file.
        Uses the first order ID from the raw event as the key for tracking.
        
        Args:
            message (dict): Position change message containing updated position status
        """

        if message['msg']['status'] == 'closed':
            self.position_info[int(message["msg"]["raw_event"]["order_ids"][0])] = \
            self.trade_outcome_checker.check_trade_outcome(message['msg'])

        # Save position data to file for debugging/logging purposes
        # self._save_data(message['msg'], 'positions')


    def _handle_option_opened(self, message):
        """
        Handle digital option placement confirmation messages.
        Formats and posts a VIP-style trade signal to Telegram.
        """
        msg = message['msg']

        expiration_time = msg['expiration_time']
        open_time_ms = msg['open_time_millisecond']
        open_time_seconds = open_time_ms / 1000

        duration_seconds = expiration_time - open_time_seconds
        duration_minutes = duration_seconds / 60

        direction = msg['direction'].upper()
        direction_label = "BUY (CALL)" if direction == "CALL" else "SELL (PUT)"
        asset_display = msg['active'].replace('-op', '')  # EURUSD-op -> EURUSD
        live_otc = msg['active'].split('-')[-1]  # EURUSD-op -> EURUSD
        market = "Live Market" if live_otc == "op" else "OTC Market"

        signal_text = (
            f"⚡ <b>BYTECODE TRADER VIP SIGNAL</b> ⚡\n\n"
            f"📊 <b>ID:</b> {msg['option_id']}\n"
            f"📊 <b>Pair:</b> {asset_display}\n"
            f"📈 <b>Direction:</b> {direction_label}\n"
            f"🕐 <b>Expiry:</b> {duration_minutes:.0f} Minutes\n"
            f"🅿️ <b>Market:</b> {market}\n\n"
            f"🔴 <b>ENTER NOW</b> 🔴"
        )

        logger.info(f"Signal | {msg['option_id']} | {asset_display} | {direction_label}")

        if self.telegram_notifier:
            self.telegram_notifier.send(signal_text)

        # Keep option_id -> asset/direction mapping for the result message later
        self.position_info.setdefault('open_meta', {})[msg['option_id']] = {
            'asset': asset_display,
            'direction': direction_label,
        }

    def _handle_socket_option_closed(self, message):
        """
        Handle position close messages. Posts VIP-style result to Telegram.
        """
        msg = message["msg"]
        order_id = int(msg["id"])

        self.position_info[order_id] = self.trade_outcome_checker.check_trade_outcome(msg)

        win = msg.get("win")  # 'win', 'loose', or 'equal'
        asset_display = msg.get("active", "N/A").replace('-op', '')
        direction = str(msg.get("dir", "N/A")).upper()
        direction_label = "BUY (CALL)" if direction == "CALL" else "SELL (PUT)"
        direction_label = "CALL" if direction == "CALL" else "PUT"

        if win == "win":
            status_text = "ITM (WON) ✅"
            status_text = "ITM ✅"
        elif win == "loose":
            status_text = "OTM (LOST) ❌"
            status_text = "OTM ❌"
        else:
            status_text = f"{(win or 'UNKNOWN').upper()} ➖"

        result_text = (
            f"⚡ <b>BYTECODE TRADER VIP RESULT</b> ⚡\n\n"
            f"📊 <b>{order_id}: {asset_display} {direction_label} {status_text}</b>\n\n"
            # f"📊 <b>Pair: {asset_display}\n"
            # f"📈 <b>Direction:</b> {direction_label}\n"
            # f"🛡️ <b>Status:</b> {status_text}\n\n"
            f"💎 <b>Premium Accuracy</b> 💎"
        )

        logger.info(f"Result | {order_id} | {asset_display} | {status_text}")

        if self.telegram_notifier:
            self.telegram_notifier.send(result_text)

    # def _handle_candles_generated(self, message: dict) -> None:
    #     # Convert IQ Option asset ID → asset name
    #     asset = str(self.asset_id_map.get(message["msg"]["active_id"]))
    #     # print(message)
    #     # Store candle in rolling dictionary
    #     self.dict_queue_add(
    #         self.real_time_candles,
    #         1,        # Maximum number of candles to store per asset/timeframe
    #         asset,
    #         int(message["msg"]["size"]),
    #         int(message["msg"]["from"]),
    #         message["msg"]
    #     )

    #     # Signal that at least one candle has been received
    #     # (used to confirm successful subscription)
    #     self.candle_generated_check[asset][int(message["msg"]["size"])] = True

        # # DEBUG: Verify storage
        # print(f"📊 Stored candle for {asset} at {int(message['msg']['from'])}")
        # print(f"   real_time_candles[{asset}][60] keys: {self.real_time_candles[asset][60].keys()}")



    # def _handle_candles_generated(self, message: dict) -> None:
    #     asset = str(self.asset_id_map.get(message["msg"]["active_id"]))
    #     timestamp = int(message["msg"]["from"])
        
    #     # Only store if this timestamp is new
    #     if timestamp not in self.real_time_candles.get(asset, {}).get(int(message["msg"]["size"]), {}):
    #         self.dict_queue_add(
    #             self.real_time_candles,
    #             1,
    #             asset,
    #             int(message["msg"]["size"]),
    #             timestamp,
    #             message["msg"]
    #         )
    #         print(f"📊 Stored NEW candle for {asset} at {timestamp}")
        
    #     self.candle_generated_check[asset][int(message["msg"]["size"])] = True


    def _handle_candles_generated(self, message: dict):
        """
        Handle candle-generated messages.
        DOES NOTHING except forward to candle manager.
        """
        if self.candle_manager:
            self.candle_manager.on_candle_message(message)