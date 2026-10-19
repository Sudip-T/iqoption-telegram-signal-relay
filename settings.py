import os
from dotenv import load_dotenv
from dataclasses import dataclass
from iqoptionapiv2.models import OptionType
from typing import Dict, List, Optional, Tuple

load_dotenv(dotenv_path=os.environ.get('ENV_FILE', '.env'))


class Config:
    # ── IQ Option ─────────────────────────────────────────────────
    IQ_EMAIL        : str   = os.getenv('IQ_EMAIL', '')
    IQ_PASSWORD     : str   = os.getenv('IQ_PASSWORD', '')
    IQ_ACCOUNT_TYPE : str   = os.getenv('IQ_ACCOUNT_TYPE', 'demo')

    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
    telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')

    # ── Retry / fallback on connection failure ─────────────────────
    RETRY_PHASE1_INTERVAL_S : int = 30
    RETRY_PHASE1_DURATION_S : int = 300
    RETRY_PHASE2_INTERVAL_S : int = 300
    RETRY_PHASE2_COUNT      : int = 3