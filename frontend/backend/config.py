"""Settings for the web app. Everything comes from the environment or the repository's .env (git-ignored)."""
import os
import pathlib

FRONTEND = pathlib.Path(__file__).resolve().parent.parent
REPO = FRONTEND.parent


def load_dotenv(path=REPO / ".env"):
    """Read KEY=VALUE lines into the environment without overriding anything already set. Values are never logged."""
    if os.environ.get("COHORT_STUDIO_NO_DOTENV") or not path.exists():
        return False
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.replace("export ", "").strip(), value.strip().strip('"').strip("'"))
    return True


class Settings:
    def __init__(self, **overrides):
        self.dotenv_loaded = load_dotenv()
        env = os.environ
        self.data_dir = overrides.get("data_dir") or env.get("COHORT_STUDIO_DATA_DIR") or str(REPO / "data" / "tcga-brca")
        default_db = (FRONTEND / ".data" / "cohort_studio.db").as_posix()
        self.database_url = overrides.get("database_url") or env.get("DATABASE_URL") or f"sqlite:///{default_db}"
        self.ai_enabled = overrides.get("ai_enabled", env.get("COHORT_STUDIO_AI", "on").lower() not in ("off", "0", "false"))
        # A ceiling on paid model calls per server run, so a low-balance key can't be drained by accident.
        self.ai_call_limit = int(overrides.get("ai_call_limit") or env.get("COHORT_STUDIO_AI_CALL_LIMIT", 200))
