import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "test")
import apnewslivebot


def test_calculate_delay_no_extension():
    # If the cycle exceeded the interval, delay should be zero
    assert apnewslivebot.calculate_delay(10, 12) == 0
    # Otherwise delay is interval minus elapsed
    assert apnewslivebot.calculate_delay(10, 4) == 6
