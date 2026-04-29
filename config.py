import json
import os


def load_env_file(path=".env"):
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_env_file()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash-latest")
FALLBACK_GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

MAX_EMAILS = int(os.getenv("MAX_EMAILS", "20"))
TOKEN_FILE = os.getenv("TOKEN_FILE", "token.pickle")
CREDS_FILE = os.getenv("CREDS_FILE", "credentials.json")
OAUTH_REDIRECT_PORT = int(os.getenv("OAUTH_REDIRECT_PORT", "8080"))
OAUTH_LOCAL_SERVER_TIMEOUT = int(
    os.getenv("OAUTH_LOCAL_SERVER_TIMEOUT", "180"))
API_REQUEST_DELAY = float(os.getenv("API_REQUEST_DELAY", "1.0"))
SKIP_DRAFT_CREATION = os.getenv(
    "SKIP_DRAFT_CREATION", "false").lower() == "true"
REPORTS_DIR = os.getenv("REPORTS_DIR", "reports")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
]

LABEL_MAP = {
    "urgent": {"name": "AI/Urgent", "color": {"backgroundColor": "#cc0000", "textColor": "#ffffff"}},
    "important": {"name": "AI/Important", "color": {"backgroundColor": "#ff9900", "textColor": "#000000"}},
    "normal": {"name": "AI/Normal", "color": {"backgroundColor": "#2da44e", "textColor": "#ffffff"}},
    "low": {"name": "AI/Low", "color": {"backgroundColor": "#999999", "textColor": "#ffffff"}},
}


def get_oauth_project_id():
    """Best-effort read of project_id from OAuth credentials file."""
    try:
        with open(CREDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "installed" in data:
            return data["installed"].get("project_id")
        if "web" in data:
            return data["web"].get("project_id")
    except Exception:
        return None
    return None
