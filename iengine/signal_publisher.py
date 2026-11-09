import html

class SignalPublisher:
    def __init__(self, notifier):
        self.notifier = notifier
        self._signal_msg_ids = {}     # option_id -> Future[message_id]
        self._seen_open, self._seen_closed = set(), set()

    def on_opened(self, t):
        if t.option_id in self._seen_open:        # ws replays after reconnect
            return
        self._seen_open.add(t.option_id)
        label = "BUY (CALL)" if t.direction == "CALL" else "SELL (PUT)"
        text = (
            "⚡ <b>BYTECODE TRADER VIP SIGNAL</b> ⚡\n\n"
            f"📊 <b>ID:</b> {t.option_id}\n"
            f"📊 <b>Pair:</b> {html.escape(t.asset)}\n"
            f"📈 <b>Direction:</b> {label}\n"
            f"🕐 <b>Expiry:</b> {t.expiry_min:.0f} Minutes\n"
            f"🅿️ <b>Market:</b> {'OTC Market' if t.is_otc else 'Live Market'}\n\n"
            "🔴 <b>ENTER NOW</b> 🔴"
        )
        self._signal_msg_ids[t.option_id] = self.notifier.send(text)

    def on_closed(self, r):
        if r.order_id in self._seen_closed:
            return
        self._seen_closed.add(r.order_id)
        status = {"win": "ITM ✅", "loose": "OTM ❌"}.get(r.result, f"{r.result.upper()} ➖")
        fut = self._signal_msg_ids.pop(r.order_id, None)
        reply_to = fut.result(timeout=0) if fut and fut.done() else None
        self.notifier.send(
            f"⚡ <b>BYTECODE TRADER VIP RESULT</b> ⚡\n\n"
            f"📊 <b>{r.order_id}: {html.escape(r.asset)} {r.direction} {status}</b>\n\n"
            "💎 <b>Premium Accuracy</b> 💎",
            reply_to=reply_to,
        )