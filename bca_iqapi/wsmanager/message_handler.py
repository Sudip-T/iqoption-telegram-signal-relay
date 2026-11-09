import json
import logging
logger = logging.getLogger(__name__)
from bca_iqapi.state import appstate
from collections import defaultdict, deque
from bca_iqapi.models import TradeOutcomeChecker
from bca_iqapi.candles import CandleSubscriptionManager
from bca_iqapi.models import TradeOpened, TradeClosed

def nested_dict(n, type):
    if n == 1:
        return defaultdict(type)
    else:
        return defaultdict(lambda: nested_dict(n - 1, type))


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
        self.underlying_list = None
        self._underlying_assests = None
        self.initialization_data = None
        self.hisory_positions = None
        self.position_info = {}
        self.traders_mood = {}
        self.orders_confirmation = {}
        self.open_meta = {}          # trade id -> {asset, direction}
        self.candles = None
        self.candle_manager = candle_manager
        self.trade_outcome_checker = TradeOutcomeChecker()

        self.on_trade_opened = None   # callable(TradeOpened)
        self.on_trade_closed = None   # callable(TradeClosed)

    def _emit(self, cb, event):
        if cb:
            try:
                cb(event)
            except Exception:
                logger.exception("Event callback failed")   # never kill the ws loop

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
        # if message_name == 'option-opened':
        #     print(message)

        # Map message names to their corresponding handler methods
        handlers = {
            'profile': self._handle_profile,
            'candles': self._handle_candles,
            'balances': self._handle_balances,
            'balance-changed': self._handle_balance_changed,
            'timeSync': self._handle_server_time,
            'underlying-list': self._handle_underlying_list,
            'initialization-data': self._handle_initialization_data,
            'training-balance-reset': self._handle_training_balance_reset,
            "history-positions":self._handle_position_history,
            "digital-option-placed":self._handle_option_opened,
            "position-changed":self._handle_position_changed,
            "option":self._handle_option_opened,
            "option-opened":self._handle_option_opened2,

            "socket-option-closed":self._handle_socket_option_closed,
            "candle-generated":self._handle_candles_generated,
            # "candle-generated": self._on_candle,
            "traders-mood-changed":self._handle_traders_mood_changed,
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
        appstate.server_time = message['msg']
    
    def _handle_profile(self, message):
        """
        Handle user profile messages and extract active balance ID for demo account.
        
        Processes profile data and identifies the demo account balance ID (type 4).
        Real account balance has type 1, demo account has type 4.
        
        Args:
            message (dict): Profile message containing user account information and balances
        """
        appstate.profile_msg = message
        for balance in message['msg']['balances']:
            if balance['type'] == appstate.balance_type:
                appstate.update(balance_id=balance['id'])
            appstate.account_list['type'] = balance

    def _handle_balances(self, message):
        """
        Handle balance update messages.
        
        Args:
            message (dict): Message containing current balance information
        """
        balance = next(
            (account['amount'] for account in message['msg']
            if account['type'] == appstate.balance_type),
            None
        )
        appstate.update(balance=balance, balance_data=message['msg'])

    def _handle_balance_changed(self, message):
        appstate.update(balance=message['msg']['current_balance']['amount'])
    
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
        self._underlying_assests = message['msg']
    
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

    def _handle_option_opened(self, message):
        """
        Handle digital option placement confirmation messages.
        
        Stores the option ID or error message based on the placement result.
        Uses request_id as the key to track placement requests.
        
        Args:
            message (dict): Placement confirmation containing either option ID or error message
        """

        if message["msg"].get("id") != None: # Successful placement - store the option ID
            self.orders_confirmation[message["request_id"]] = message["msg"].get("id")
        else: # Failed placement - store the error message
            self.orders_confirmation[message["request_id"]] = message["msg"].get("message")
        
    def _handle_option_opened2(self, message):
        """
        Order placement confirmation ('option' / 'digital-option-placed').
        Success -> emit TradeOpened. Failure -> store the error message.
        """
        msg = message["msg"]
        if msg.get("option_id") is None:
            return

        try:
            trade_id = msg["option_id"]    # NOTE: results are keyed by this same id (see _publish_closed)
            active = msg["active"]
            expiry_min = round((msg["expiration_time"] - msg["open_time_millisecond"] / 1000) / 60)
            direction = "CALL" if str(msg["direction"]).upper() == "CALL" else "PUT"
        except KeyError as e:
            logger.error("Unexpected order payload (missing %s): %s", e, msg)
            return

        asset = active.replace("-op", "")
        is_otc = active.split("-")[-1] != "op"
        self.open_meta[trade_id] = {"asset": asset, "direction": direction}

        logger.info(f"Signal | {trade_id} | {asset} | {direction}")
        self._emit(self.on_trade_opened,
                   TradeOpened(trade_id, asset, is_otc, direction, expiry_min))

    def _handle_position_changed(self, message):
        """Digital options close through here ('status' == 'closed')."""
        msg = message["msg"]
        if msg.get("status") != "closed":
            return
        try:
            order_id = int(msg["raw_event"]["order_ids"][0])
            outcome = self.trade_outcome_checker.check_trade_outcome(msg)
        except Exception:
            logger.exception("Could not process closed position: %s", msg)
            return
        self.position_info[order_id] = outcome
        self._publish_closed(order_id, outcome)

    def _handle_socket_option_closed(self, message):
        """Binary/turbo options close through here."""
        msg = message["msg"]
        try:
            order_id = int(msg["id"])
            outcome = self.trade_outcome_checker.check_trade_outcome(msg)
        except Exception:
            logger.exception("Could not process closed option: %s", msg)
            return
        self.position_info[order_id] = outcome
        self._publish_closed(order_id, outcome, result=msg.get("win"))  # 'win'/'loose'/'equal'

    def _publish_closed(self, order_id, outcome, result=None):
        meta = self.open_meta.pop(order_id, {})
        asset = meta.get("asset") or str(outcome.asset).replace("-op", "")
        direction = str(meta.get("direction") or outcome.direction).upper()
        direction = "CALL" if direction == "CALL" else "PUT"
        if result is None:
            result = "win" if outcome.is_win else "loss" if outcome.is_loss else "equal"

        logger.info(f"Result | {order_id} | {asset} | {result}")
        self._emit(self.on_trade_closed, TradeClosed(order_id, asset, direction, result or "unknown"))

    def _handle_candles_generated(self, message: dict):
        """
        Handle candle-generated messages.
        DOES NOTHING except forward to candle manager.
        """
        if self.candle_manager:
            self.candle_manager.on_candle_message(message)

    def _handle_traders_mood_changed(self, message: dict):
        self.traders_mood[message["msg"]["asset_id"]]=message["msg"]["value"]