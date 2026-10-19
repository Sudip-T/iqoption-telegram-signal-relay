# iqoptionapiv2/notifications/telegram_notifier.py
import logging
import queue
import threading
import time
import requests
from settings import Config

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Telegram bot sender backed by a single background worker thread
    consuming a queue, so bursts of signals are sent in order without
    spawning a thread per message or hammering Telegram's rate limits.
    """

    def __init__(self, maxsize: int = 1000):
        self.config = Config()
        self.bot_token = self.config.telegram_token
        self.chat_id = self.config.telegram_chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        self._session = requests.Session()
        self._queue: "queue.Queue[tuple[str, str] | None]" = queue.Queue(maxsize=maxsize)
        self._stop_event = threading.Event()

        self._worker = threading.Thread(
            target=self._run, name="TelegramNotifierWorker", daemon=True
        )
        self._worker.start()

    def send(self, text: str, parse_mode: str = "HTML") -> None:
        """Enqueue a message. Never blocks the caller (ws message loop)."""
        try:
            self._queue.put_nowait((text, parse_mode))
        except queue.Full:
            logger.error("Telegram queue full — dropping message: %s", text[:80])

    def _run(self) -> None:
        """Worker loop: pulls one message at a time, sends it, respects rate limits."""
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            if item is None:  # shutdown sentinel
                break

            text, parse_mode = item
            self._send_with_retry(text, parse_mode)
            self._queue.task_done()

            # Telegram allows ~30 msgs/sec globally, ~1/sec per chat is a safe default.
            time.sleep(0.05)

    def _send_with_retry(self, text: str, parse_mode: str, max_retries: int = 3) -> None:
        for attempt in range(max_retries):
            try:
                resp = self._session.post(
                    self.api_url,
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                    timeout=10,
                )

                if resp.status_code == 200:
                    return

                if resp.status_code == 429:
                    # Telegram tells us how long to back off
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 1)
                    logger.warning("Telegram rate-limited — sleeping %ss", retry_after)
                    time.sleep(retry_after)
                    continue

                logger.error("Telegram send failed [%s]: %s", resp.status_code, resp.text)
                return

            except Exception as e:
                logger.error("Telegram send error (attempt %d/%d): %s", attempt + 1, max_retries, e)
                time.sleep(1)

        logger.error("Telegram send gave up after %d attempts: %s", max_retries, text[:80])

    def close(self, wait: bool = True) -> None:
        """Call on app shutdown to flush and stop the worker cleanly."""
        if wait:
            self._queue.join()
        self._stop_event.set()
        self._queue.put(None)
        self._worker.join(timeout=5)