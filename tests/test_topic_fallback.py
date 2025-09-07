import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apnewslivebot import topic_only_hashtags

def test_top25(): assert topic_only_hashtags("AP Top 25")==["#Top25"]

def test_live_strip(): assert topic_only_hashtags("LIVE: Israel-Gaza updates")==["#IsraelGazaUpdates"]

def test_punct(): assert topic_only_hashtags("AP   Top—25!! ")==["#Top25"]
