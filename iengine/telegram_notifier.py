import logging
import queue
import threading
import time
from concurrent.futures import Future

import requests

logger = logging.getLogger(__name__)

MAX_LEN = 4000            # Telegram's hard limit is 4096 chars
_STOP = object()          # shutdown sentinel


def _split(text: str, limit: int = MAX_LEN) -> list:
    """Split long text on line breaks so HTML tags stay intact."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                    # pathological single line
            if cur:
                chunks.append(cur.rstrip("\n"))
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur.rstrip("\n"))
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur.rstrip("\n"))
    return chunks


class TelegramNotifier:
    """
    Telegram sender backed by ONE worker thread + queue.
    send()/send_photo() never block the caller and return a Future that
    resolves to the Telegram message_id (or None if it failed), so a later
    message can reply to an earlier one.
    """

    def __init__(self, config, maxsize: int = 1000):
        self.bot_token = config.telegram_token
        self.chat_id = config.telegram_chat_id
        self.enabled = bool(self.bot_token and self.chat_id)
        if not self.enabled:
            logger.warning("Telegram disabled: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")

        self._base = f"https://api.telegram.org/bot{self.bot_token}"
        self._session = requests.Session()
        self._queue: "queue.Queue" = queue.Queue(maxsize=maxsize)
        self._worker = threading.Thread(
            target=self._run, name="TelegramNotifierWorker", daemon=True
        )
        self._worker.start()

    # ── Public API ────────────────────────────────────────────────────────
    def send(self, text: str, parse_mode: str = "HTML",
             reply_to: int = None, chat_id=None) -> Future:
        first = None
        for i, chunk in enumerate(_split(text)):
            fut = self._enqueue("text", chunk, parse_mode,
                                reply_to if i == 0 else None, chat_id)
            if first is None:
                first = fut
        return first

    def send_photo(self, image, caption: str = "", parse_mode: str = "HTML",
                   reply_to: int = None, chat_id=None) -> Future:
        """image: BytesIO (e.g. from generate_signal_card) or raw PNG bytes."""
        data = image.getvalue() if hasattr(image, "getvalue") else image
        return self._enqueue("photo", (data, caption), parse_mode, reply_to, chat_id)

    def close(self, timeout: float = 10.0) -> None:
        """Flush pending messages (up to `timeout` s) and stop the worker."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.1)
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            pass
        self._worker.join(timeout=2)

    # ── Internals ─────────────────────────────────────────────────────────
    def _enqueue(self, kind, payload, parse_mode, reply_to, chat_id) -> Future:
        fut: Future = Future()
        if not self.enabled:
            fut.set_result(None)
            return fut
        try:
            self._queue.put_nowait(
                (kind, payload, parse_mode, reply_to, chat_id or self.chat_id, fut)
            )
        except queue.Full:
            logger.error("Telegram queue full — dropping message")
            fut.set_result(None)
        return fut

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                kind, payload, parse_mode, reply_to, chat_id, fut = item
                try:
                    msg_id = self._post(kind, payload, parse_mode, reply_to, chat_id)
                except Exception as e:              # never let the worker die
                    logger.error("Telegram worker error: %s", self._redact(e))
                    msg_id = None
                fut.set_result(msg_id)
            finally:
                self._queue.task_done()

            if not self._queue.empty():
                time.sleep(1.0)     # ~1 msg/sec per chat is Telegram's safe rate

    def _post(self, kind, payload, parse_mode, reply_to, chat_id,
              max_retries: int = 3, max_429: int = 5):
        attempts = rate_limited = 0
        while attempts < max_retries:
            try:
                if kind == "photo":
                    png, caption = payload
                    data = {"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode}
                    if reply_to:
                        data["reply_to_message_id"] = reply_to
                        data["allow_sending_without_reply"] = True
                    resp = self._session.post(
                        f"{self._base}/sendPhoto", data=data,
                        files={"photo": ("signal.png", png, "image/png")}, timeout=20,
                    )
                else:
                    body = {"chat_id": chat_id, "text": payload,
                            "parse_mode": parse_mode, "disable_web_page_preview": True}
                    if reply_to:
                        body["reply_to_message_id"] = reply_to
                        body["allow_sending_without_reply"] = True
                    resp = self._session.post(f"{self._base}/sendMessage", json=body, timeout=10)

                if resp.status_code == 200:
                    return resp.json()["result"]["message_id"]

                if resp.status_code == 429:         # rate limit: doesn't use up a retry
                    rate_limited += 1
                    if rate_limited > max_429:
                        break
                    wait = resp.json().get("parameters", {}).get("retry_after", 1)
                    logger.warning("Telegram rate-limited — sleeping %ss", wait)
                    time.sleep(wait)
                    continue

                logger.error("Telegram send failed [%s]: %s", resp.status_code, resp.text[:200])
                return None

            except Exception as e:
                attempts += 1
                logger.error("Telegram send error (attempt %d/%d): %s",
                             attempts, max_retries, self._redact(e))
                time.sleep(1)

        logger.error("Telegram send gave up")
        return None

    def _redact(self, err) -> str:
        """requests exceptions embed the full URL — which contains the bot token."""
        s = str(err)
        return s.replace(self.bot_token, "<token>") if self.bot_token else s