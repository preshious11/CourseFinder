import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
os.environ["TUTORIALBAR"] = "false"
os.environ["COUPON_FEEDS"] = ""
os.environ["UDEMY_CLIENT_ID"] = ""
os.environ["UDEMY_CLIENT_SECRET"] = ""
