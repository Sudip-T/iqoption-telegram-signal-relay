# IQ Option Telegram Signal Bot

A Python bot that watches your IQ Option account and posts every trade to a Telegram channel as a clean signal, then follows up with the result.

When a position opens, the bot sends a signal (pair, direction, expiry, market type) so your followers can enter the same trade within seconds. When it closes, the result (ITM / OTM / equal) is posted as a **reply to the original signal**, which keeps the channel easy to follow.

> **Risk notice:** Trading binary and digital options carries a high risk of loss. This software is for educational purposes and is not financial advice. Test on a demo account first and never trade money you can't afford to lose. If you share signals publicly, you are responsible for following the laws and platform rules that apply to you.

## Features

- **Instant signals** as soon as IQ Option confirms a trade
- **Result messages** threaded under the original signal
- **Digital and binary options**, live and OTC markets (labelled automatically)
- **Non-blocking delivery:** a background worker sends messages in order, with retries and Telegram rate-limit handling
- **Signal priority:** signals are queued ahead of results and admin alerts
- **Duplicate protection:** replayed websocket events are announced only once
- **Private admin alerts (optional):** start, stop and reconnect notices go to a separate chat, never the signals channel
- **Network monitor** that detects internet drops and pauses/resumes the bot
- **Token-safe logging:** the bot token is kept out of `system.log`
- Demo and real account support

## How it works

```mermaid
flowchart LR
    A[IQ Option websocket] --> B[MessageHandler]
    B -- TradeOpened / TradeClosed --> C[SignalPublisher]
    C --> D[TelegramNotifier queue]
    D --> E[Telegram channel]
```

1. `cba_iqapi` connects to IQ Option and receives trade messages.
2. `MessageHandler` turns them into `TradeOpened` / `TradeClosed` events.
3. `SignalPublisher` formats the messages and links each result to its signal.
4. `TelegramNotifier` sends them from a single background thread.

The websocket layer knows nothing about Telegram, so you can swap or extend the output (Discord, a database, etc.) by subscribing to the same two events.

## Project structure

```
main.py                  entry point and app lifecycle
settings.py              configuration, loaded from .env
card_generator.py        builds signal card images (not wired in yet)
expiry.py                expiry/duration helpers
cba_iqapi/               IQ Option API wrapper (http, wsmanager, instruments, trade, ...)
iengine/
├── _iq_engine.py        connection handling for the IQ Option client
├── network_monitor.py   internet connectivity checks
├── signal_publisher.py  formats signals/results
└── telegram_notifier.py queued, rate-limit-aware Telegram sender
```

## Requirements

- A recent Python 3 release (3.11+ recommended)
- An IQ Option account (a demo account works)
- A Telegram bot token from [@BotFather](https://t.me/BotFather), with the bot added as an admin of your channel

## Installation

```bash
git clone https://github.com/<your-user>/iqoption-telegram-signal-bot.git
cd iqoption-telegram-signal-bot
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Create a `.env` file next to `main.py`:

```env
IQ_EMAIL=you@example.com
IQ_PASSWORD=your-password
IQ_ACCOUNT_TYPE=demo              # demo or real
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=-100xxxxxxxxxx   # your signals channel
TELEGRAM_ADMIN_CHAT_ID=           # optional: private chat for status alerts
```

To use `TELEGRAM_ADMIN_CHAT_ID`, add this line to `settings.py` next to the other Telegram settings:

```python
telegram_admin_chat_id = os.getenv('TELEGRAM_ADMIN_CHAT_ID')
```

> Never commit `.env` or `system.log`. Both are listed in `.gitignore` below.

To find a channel's chat ID, post a message in it and open `https://api.telegram.org/bot<TOKEN>/getUpdates`.

## Usage

```bash
python main.py                    # uses IQ_ACCOUNT_TYPE from .env
python main.py --account demo     # override the account type
python main.py --account real
```

Open a trade on IQ Option and the signal appears in your channel. Stop the bot with `Ctrl+C`; queued messages are flushed before it exits.

### What your followers see

```
⚡ BYTECODE TRADER VIP SIGNAL ⚡

📊 ID: 14433273528
📊 Pair: EURUSD
📈 Direction: BUY (CALL)
🕐 Expiry: 1 Minutes
🅿️ Market: Live Market

🔴 ENTER NOW 🔴
```

```
⚡ BYTECODE TRADER VIP RESULT ⚡

📊 14433273528: EURUSD CALL ITM ✅

💎 Premium Accuracy 💎
```

Edit the text in `iengine/signal_publisher.py` to change the branding or layout.

## Logging

Logs go to the console (INFO) and `system.log` (DEBUG, overwritten on each start). Each signal also logs its delivery time, for example `Signal 14433273528 delivered to Telegram in 412 ms`. `urllib3` and `requests` are kept at WARNING so the bot token never appears in the log.

## Troubleshooting

| Problem | Check |
|---|---|
| Nothing arrives in Telegram | Is the bot an admin of the channel? Is `TELEGRAM_CHAT_ID` correct (channels start with `-100`)? |
| `Telegram disabled` in the log | `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` is empty |
| `Invalid Account Type` | `IQ_ACCOUNT_TYPE` must be `demo` or `real` |
| IQ Option won't connect | Check email/password; the bot waits up to 30 seconds on start |
| Signals delayed during bursts | Telegram allows about one message per second per chat; the bot backs off automatically on rate limits |

## Recommended `.gitignore`

```
.env
system.log
__pycache__/
.venv/
```

## Contributing

Issues and pull requests are welcome. Please test changes on a demo account.

## License

[Add your license here, e.g. MIT]

## Links

- Website: [bytecodeautomation.com](https://www.bytecodeautomation.com)
- YouTube: [@BytecodeAutomation](https://www.youtube.com/@BytecodeAutomation)
- Telegram: [t.me/BytecodeAutomation](https://t.me/BytecodeAutomation)
- IQ Option API: [iqoptionapi-sync](https://github.com/Sudip-T/iqoptionapi-sync)