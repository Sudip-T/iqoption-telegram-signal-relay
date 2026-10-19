# iqoptionapiv2/notifications/card_generator.py
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# Colors matching the screenshot's dark theme
BG_COLOR = (24, 24, 28)
CARD_COLOR = (30, 30, 36)
TEXT_WHITE = (255, 255, 255)
TEXT_RED = (255, 90, 90)
TEXT_GREEN = (90, 220, 130)
TEXT_GRAY = (150, 150, 155)
GOLD = (255, 200, 60)

CARD_WIDTH = 500
PADDING = 30
LINE_HEIGHT = 50


def _load_fonts():
    """
    Loads bold/regular fonts. Falls back to PIL's default bitmap font
    if DejaVuSans-Bold isn't found (ships with Pillow on most installs).
    """
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 26)
        label_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
        value_font = ImageFont.truetype("DejaVuSans.ttf", 20)
    except OSError:
        title_font = ImageFont.load_default()
        label_font = ImageFont.load_default()
        value_font = ImageFont.load_default()
    return title_font, label_font, value_font


def _draw_icon_square(draw, x, y, color, size=22):
    """Small colored square as an icon placeholder (bar-chart style icon)."""
    draw.rounded_rectangle([x, y, x + size, y + size], radius=4, fill=color)


def _rows_height(n_rows: int) -> int:
    return PADDING * 2 + 60 + n_rows * LINE_HEIGHT + 50


def generate_signal_card(pair: str, direction_label: str, expiry_minutes: float,
                          broker: str = "Live Market") -> BytesIO:
    """Generate a VIP-signal style card image. Returns PNG bytes buffer."""
    title_font, label_font, value_font = _load_fonts()
    rows = [
        ("Pair:", pair, TEXT_RED, (80, 180, 255)),
        ("Direction:", direction_label, TEXT_RED, (80, 180, 255)),
        ("Expiry:", f"{expiry_minutes:.0f} Minutes", TEXT_RED, TEXT_GRAY),
        ("Broker:", broker, TEXT_RED, TEXT_GRAY),
    ]
    height = _rows_height(len(rows))
    img = Image.new("RGB", (CARD_WIDTH, height), BG_COLOR)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [10, 10, CARD_WIDTH - 10, height - 10], radius=20, fill=CARD_COLOR
    )

    y = PADDING + 10
    draw.text((PADDING, y), "⚡ SS7TRADER VIP SIGNAL ⚡", font=title_font, fill=GOLD)
    y += 55

    for label, value, icon_color, value_color in rows:
        _draw_icon_square(draw, PADDING, y + 2, icon_color)
        draw.text((PADDING + 34, y), label, font=label_font, fill=TEXT_WHITE)
        draw.text((PADDING + 34 + draw.textlength(label + " ", font=label_font), y),
                   value, font=value_font, fill=TEXT_RED)
        y += LINE_HEIGHT

    y += 10
    draw.rounded_rectangle([PADDING, y, CARD_WIDTH - PADDING, y + 45], radius=10, fill=(180, 30, 30))
    draw.text((PADDING + 20, y + 10), "🔴 ENTER NOW 🔴", font=label_font, fill=TEXT_WHITE)

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_result_card(pair: str, direction_label: str, won: bool) -> BytesIO:
    """Generate a VIP-result style card image. Returns PNG bytes buffer."""
    title_font, label_font, value_font = _load_fonts()
    status_text = "ITM (WON)" if won else "OTM (LOST)"
    status_color = TEXT_GREEN if won else TEXT_RED
    status_icon = "✅" if won else "❌"

    rows = [
        ("Pair:", pair, TEXT_RED, (80, 180, 255)),
        ("Direction:", direction_label, TEXT_RED, (80, 180, 255)),
        ("Status:", f"{status_text} {status_icon}", status_color, (255, 100, 100)),
    ]
    height = _rows_height(len(rows))
    img = Image.new("RGB", (CARD_WIDTH, height), BG_COLOR)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [10, 10, CARD_WIDTH - 10, height - 10], radius=20, fill=CARD_COLOR
    )

    y = PADDING + 10
    draw.text((PADDING, y), "⚡ SS7TRADER VIP RESULT ⚡", font=title_font, fill=GOLD)
    y += 55

    for label, value, icon_color, value_color in rows:
        _draw_icon_square(draw, PADDING, y + 2, icon_color)
        draw.text((PADDING + 34, y), label, font=label_font, fill=TEXT_WHITE)
        draw.text((PADDING + 34 + draw.textlength(label + " ", font=label_font), y),
                   value, font=value_font, fill=status_color if label == "Status:" else TEXT_RED)
        y += LINE_HEIGHT

    y += 10
    draw.text((PADDING, y), "💎 Premium Accuracy 💎", font=label_font, fill=(120, 180, 255))

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf