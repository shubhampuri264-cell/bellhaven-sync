"""Settings for the whole project. Every tunable value lives here."""
import os
from pathlib import Path

ROOT = Path(__file__).parent


def _load_dotenv(path=ROOT / ".env"):
    """Read KEY=VALUE lines from .env into the environment (real env vars win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

BASE_URL = os.environ.get("BELLHAVEN_BASE_URL", "https://analyst-assessment-production.up.railway.app")
API_URL = BASE_URL + "/api/v1"
API_TOKEN = os.environ.get("CRM_API_TOKEN", "")
DB_PATH = Path(os.environ.get("SYNC_DB", ROOT / "data" / "sync.db"))

# The operator whose website we sync. Its CRM parent account is found by this name.
OPERATOR_NAME = "Bellhaven Senior Living"

# Website care offering -> CRM care_type value.
CARE_TYPE_MAP = {
    "Assisted Living": "Assisted Living",
    "Memory Support": "Memory Care",
    "Short-Term Rehabilitation & Nursing": "Skilled Nursing",
    "Independent Living": "Independent Living",
}

# A name-only match (street does not match) needs at least this similarity, 0 to 1.
NAME_MATCH_THRESHOLD = 0.90

# Abort the run if the scrape returns fewer than this share of the previous run's
# locations. Stops a half-loaded website from flagging every account as "left".
MIN_SCRAPE_RATIO = 0.8
