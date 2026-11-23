import os

# Tests never call GitHub; test_updates.py drives the check with a fake fetch instead.
os.environ.setdefault("REWND_NO_UPDATE_CHECK", "1")
