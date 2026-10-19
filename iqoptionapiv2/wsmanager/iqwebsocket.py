import json
import time
import logging
import websocket
import threading

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Manages WebSocket connections for real-time communication with IQ Option.
    """

    def __init__(self, message_handler):
        self.ws_url = 'wss://ws.iqoption.com/echo/websocket'
        self.websocket = None
        self.send_message_count = 0
        self._is_ws_connected = False
        self._on_drop_callback = None        # fired when WS drops unexpectedly
        self._intentional_close = False      # flag so close() doesn't trigger reconnect
        self.message_handler = message_handler

    def set_drop_callback(self, callback):
        """Register a function to call when the WebSocket drops unexpectedly."""
        self._on_drop_callback = callback

    def start_websocket(self):
        self._intentional_close = False      # ← reset before every fresh start
        self._is_ws_connected = False

        self.websocket = websocket.WebSocketApp(
            self.ws_url,
            on_open=lambda ws: self._on_open(),
            on_message=lambda ws, msg: self._on_message(msg),
            on_error=lambda ws, err: self._on_error(err),
            on_close=lambda ws, code, reason: self._on_close(code, reason)
        )

        wst = threading.Thread(target=self.websocket.run_forever, daemon=True)
        wst.start()

        deadline = time.time() + 30
        while not self._is_ws_connected:
            if time.time() > deadline:
                raise RuntimeError("WebSocket did not become active within 30 seconds.")
            time.sleep(0.1)

    def send_message(self, name, msg, request_id=None):
        """
        Send a JSON message through the WebSocket connection.

        Returns:
            str: The request_id used (auto-generated if not provided).
        """
    
        if request_id is None:
            request_id = str(time.time()).split(".")[1]

        self.send_message_count += 1
        data = json.dumps({"name": name, "msg": msg, "request_id": request_id})
        
        if self.websocket and self._is_ws_connected:
            self.websocket.send(data)
            return request_id
        else:
            logger.error("WebSocket not connected. Cannot send message.")
            return None

    def close(self):
        """Intentional close — does NOT trigger reconnect callback."""
        # self._intentional_close = True
        if self.websocket:
            self.websocket.close()
            self.websocket = None
        self._is_ws_connected = False

    def _on_open(self):
        logger.debug("Websocket connected")

    def _on_message(self, message):
        try:
            parsed = json.loads(message)
            # print('parsed', parsed)
            self.message_handler.handle_message(parsed)
            self._is_ws_connected = True
        except json.JSONDecodeError as e:
            logger.warning("WebSocket JSON parse error: %s", e)
        except Exception as e:
            logger.warning("WebSocket message handling error: %s", e)

    def _on_error(self, error):
        logger.error("WebSocket error: %s", error)

    def _on_close(self, close_status_code, close_msg):
        logger.info("WebSocket closed — code: %s  msg: %s", close_status_code, close_msg)
        self._is_ws_connected = False

        # ── NEW: fire reconnect only on unexpected drops ──────────────────
        if not self._intentional_close and self._on_drop_callback:
            t = threading.Thread(target=self._on_drop_callback, daemon=True, name="WSDropReconnect")
            t.start()