from config import OAUTH_LOCAL_SERVER_TIMEOUT, OAUTH_REDIRECT_PORT, SCOPES, load_env_file
import os
import pickle
import subprocess
import sys
from urllib.parse import parse_qs, urlparse
from pathlib import Path


def bootstrap_local_venv():
    venv_python = Path(__file__).with_name(".venv") / "Scripts" / "python.exe"
    if not venv_python.exists():
        raise ModuleNotFoundError(
            "google_auth_oauthlib is not installed, and no local .venv was found. "
            "Install the project dependencies or activate the virtual environment first."
        )

    current_python = Path(sys.executable).resolve()
    target_python = venv_python.resolve()
    if current_python != target_python:
        completed = subprocess.run(
            [str(target_python), str(Path(__file__).resolve()), *sys.argv[1:]],
            check=False,
        )
        raise SystemExit(completed.returncode)


try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ModuleNotFoundError:
    bootstrap_local_venv()
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build


def resolve_creds_file():
    configured = os.getenv("CREDS_FILE", "credentials.json")
    candidates = [configured]

    if configured == "credentials.json":
        candidates.append("credentials.json.json")

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    raise FileNotFoundError(
        f"Gmail OAuth client file not found. Tried: {', '.join(candidates)}. "
        "Download the Desktop app OAuth credentials JSON from Google Cloud "
        "and place it in the project folder, or update CREDS_FILE in .env."
    )


def authorize_with_fallback(flow):
    try:
        return flow.run_local_server(
            port=OAUTH_REDIRECT_PORT,
            timeout_seconds=OAUTH_LOCAL_SERVER_TIMEOUT,
            open_browser=True,
        )
    except KeyboardInterrupt:
        print("\nOAuth callback wait interrupted. Switching to manual code entry...")
    except Exception as exc:
        print(f"\nLocal callback failed: {exc}")
        print("Switching to manual code entry...")

    redirect_uri = f"http://localhost:{OAUTH_REDIRECT_PORT}/"
    flow.redirect_uri = redirect_uri
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    print("\n1) Open this URL in your browser:")
    print(auth_url)
    print(
        "\n2) Complete sign-in. If browser cannot reach localhost, copy the full URL "
        "from the address bar after approval and paste it below."
    )
    redirected_url = input("\nPaste redirected URL: ").strip()

    if not redirected_url:
        raise RuntimeError("No redirected URL provided.")

    parsed = urlparse(redirected_url)
    params = parse_qs(parsed.query)
    code = params.get("code", [None])[0]
    if not code:
        raise RuntimeError(
            "Could not find OAuth code in the pasted URL. Ensure you paste the full URL."
        )

    flow.fetch_token(code=code)
    return flow.credentials


def setup():
    load_env_file()

    print("Starting Gmail OAuth setup...")
    print("A browser window will open. Log in with your Google account.\n")

    creds_file = resolve_creds_file()

    flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
    creds = authorize_with_fallback(flow)

    token_file = os.getenv("TOKEN_FILE", "token.pickle")
    with open(token_file, "wb") as f:
        pickle.dump(creds, f)

    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()

    print(f"\nSuccess! Connected to: {profile['emailAddress']}")
    print(f"Total messages: {profile['messagesTotal']}")
    print(f"\n{token_file} saved. You can now run: python agent.py")


if __name__ == "__main__":
    setup()
