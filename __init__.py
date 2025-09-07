import os

# Provide safe defaults so importing the package doesn't require secrets
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "test")

from .apnewslivebot import *

__all__ = [name for name in globals() if not name.startswith("_")]
