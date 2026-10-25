import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.environ.get("CTUNE_DATA", os.path.join(REPO, "data"))
TOOLS = os.path.join(REPO, "tools")
