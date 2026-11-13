"""The repository's .env (git-ignored): keys and settings for the CLI, the Streamlit app and the web server."""
import os
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent


def load_dotenv(path=REPO / ".env"):
    """Read KEY=VALUE lines into the environment without overriding anything already set. Values are never logged."""
    if os.environ.get("COHORT_STUDIO_NO_DOTENV") or not pathlib.Path(path).exists():
        return False
    for line in pathlib.Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.replace("export ", "").strip(), value.strip().strip('"').strip("'"))
    return True
