import os
import json
import base64
import pickle
import time
from datetime import datetime
from email.mime.text import MIMEText
from urllib.parse import parse_qs, urlparse

from google import genai
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from config import (
    API_REQUEST_DELAY,
    CREDS_FILE,
    FALLBACK_GEMINI_MODELS,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    LABEL_MAP,
    MAX_EMAILS,
    OAUTH_LOCAL_SERVER_TIMEOUT,
    OAUTH_REDIRECT_PORT,
    REPORTS_DIR,
    SKIP_DRAFT_CREATION,
    SCOPES,
    TOKEN_FILE,
    get_oauth_project_id,
)

# ── Gemini setup ──────────────────────────────────────────────────────────────
genai_client = genai.Client(api_key=GEMINI_API_KEY)
ACTIVE_GEMINI_MODEL = None
LAST_API_CALL_TIME = 0


def generate_gemini_text(prompt):
    global ACTIVE_GEMINI_MODEL, LAST_API_CALL_TIME

    time_since_last_call = time.time() - LAST_API_CALL_TIME
    if time_since_last_call < API_REQUEST_DELAY:
        sleep_time = API_REQUEST_DELAY - time_since_last_call
        time.sleep(sleep_time)

    candidate_models = [GEMINI_MODEL] + [
        model for model in FALLBACK_GEMINI_MODELS if model != GEMINI_MODEL
    ]

    last_exc = None
    for model_name in candidate_models:
        try:
            LAST_API_CALL_TIME = time.time()
            response = genai_client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            text = getattr(response, "text", None)
            if not text:
                raise RuntimeError("Gemini returned an empty response.")

            if ACTIVE_GEMINI_MODEL != model_name:
                ACTIVE_GEMINI_MODEL = model_name
                print(f"   Gemini model in use: {ACTIVE_GEMINI_MODEL}")

            return text.strip()
        except Exception as exc:
            last_exc = exc
            message = str(exc)
            if "RESOURCE_EXHAUSTED" in message or "quota" in message.lower():
                raise RuntimeError(
                    "Gemini quota exceeded for the configured API key/project. "
                    "Check usage and billing in Google AI Studio, then retry. "
                    "Docs: https://ai.google.dev/gemini-api/docs/rate-limits"
                ) from exc
            if "NOT_FOUND" in message or "not found" in message.lower():
                continue
            raise

    raise RuntimeError(
        "No supported Gemini model was available. "
        "Set GEMINI_MODEL in .env to one of: gemini-2.0-flash, gemini-1.5-flash, gemini-1.5-flash-8b"
    ) from last_exc


# ── Gmail Auth ────────────────────────────────────────────────────────────────
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


def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDS_FILE, SCOPES)
            creds = authorize_with_fallback(flow)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("gmail", "v1", credentials=creds)


# ── Gmail helpers ─────────────────────────────────────────────────────────────
def get_unread_emails(service, max_results=MAX_EMAILS):
    try:
        results = service.users().messages().list(
            userId="me", labelIds=["INBOX", "UNREAD"], maxResults=max_results
        ).execute()
    except HttpError as exc:
        error_message = str(exc)
        if getattr(exc, "resp", None) and exc.resp.status == 403 and "accessNotConfigured" in error_message:
            oauth_project_id = get_oauth_project_id() or "<your-project-id>"
            raise RuntimeError(
                "Gmail API is not enabled for the Google Cloud project tied to your OAuth client. "
                f"Open this URL and enable Gmail API for project {oauth_project_id}, "
                "then wait 2-5 minutes and rerun: "
                f"https://console.developers.google.com/apis/api/gmail.googleapis.com/overview?project={oauth_project_id}"
            ) from exc
        raise

    messages = results.get("messages", [])
    emails = []
    for msg in messages:
        full = service.users().messages().get(
            userId="me", id=msg["id"], format="full"
        ).execute()
        emails.append(parse_email(full))
    return emails


def parse_email(msg):
    headers = {h["name"]: h["value"]
               for h in msg["payload"].get("headers", [])}
    body = extract_body(msg["payload"])
    return {
        "id":      msg["id"],
        "subject": headers.get("Subject", "(no subject)"),
        "sender":  headers.get("From", "unknown"),
        "date":    headers.get("Date", ""),
        "snippet": msg.get("snippet", ""),
        "body":    body[:2000],   # trim to avoid token limits
        "labels":  msg.get("labelIds", []),
    }


def extract_body(payload):
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
    for part in payload.get("parts", []):
        result = extract_body(part)
        if result:
            return result
    return ""


def get_or_create_label(service, priority):
    label_info = LABEL_MAP[priority]
    existing = service.users().labels().list(
        userId="me").execute().get("labels", [])
    for lbl in existing:
        if lbl["name"] == label_info["name"]:
            return lbl["id"]

    new_label = service.users().labels().create(userId="me", body={
        "name": label_info["name"],
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
        "color": label_info["color"],
    }).execute()
    return new_label["id"]


def apply_label(service, email_id, label_id):
    service.users().messages().modify(
        userId="me", id=email_id,
        body={"addLabelIds": [label_id]}
    ).execute()


def create_draft(service, to, subject, body):
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = f"Re: {subject}"
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}
    ).execute()


# ── AI: Classify ──────────────────────────────────────────────────────────────
def classify_email(email):
    prompt = f"""You are an email triage assistant. Analyze this email and respond with ONLY a JSON object.

Email details:
- From: {email['sender']}
- Subject: {email['subject']}
- Date: {email['date']}
- Body: {email['body'] or email['snippet']}

Respond with this exact JSON structure (no markdown, no backticks):
{{
  "priority": "urgent|important|normal|low",
  "category": "work|personal|newsletter|spam|finance|meeting|support|other",
  "summary": "One sentence summary of what this email is about",
  "action_needed": true or false,
  "needs_reply": true or false,
  "urgency_reason": "Why this priority level was chosen"
}}

Priority guide:
- urgent: requires response within hours, deadline/emergency/boss
- important: needs response within 1-2 days, meaningful action required
- normal: should reply this week, routine matters
- low: newsletters, promotions, FYI emails, no reply needed"""

    text = generate_gemini_text(prompt).strip("```json").strip("```").strip()
    return json.loads(text)


# ── AI: Draft reply ───────────────────────────────────────────────────────────
def draft_reply(email, classification):
    prompt = f"""You are a professional email assistant. Write a brief, helpful reply to this email.

Original email:
- From: {email['sender']}
- Subject: {email['subject']}
- Body: {email['body'] or email['snippet']}

Email category: {classification['category']}
Summary: {classification['summary']}

Write ONLY the reply body text. Be professional but friendly.
Keep it concise (2-4 sentences). Do not include subject line or greeting header."""

    return generate_gemini_text(prompt)


# ── Main agent loop ───────────────────────────────────────────────────────────
def run_agent():
    print("\n" + "="*55)
    print("  Email Triage Agent  |  powered by Gemini")
    print("="*55)

    service = get_gmail_service()
    print(f"\nConnected to Gmail.")

    print(f"Fetching up to {MAX_EMAILS} unread emails...")
    try:
        emails = get_unread_emails(service)
    except RuntimeError as exc:
        print(f"\nConfiguration error: {exc}")
        print("Fix the Google Cloud setting above and run the agent again.")
        return
    print(f"Found {len(emails)} unread emails.\n")

    if not emails:
        print("Inbox is clear! Nothing to triage.")
        return

    results = {"urgent": [], "important": [], "normal": [], "low": []}
    drafts_created = 0

    for i, email in enumerate(emails, 1):
        print(f"[{i}/{len(emails)}] Processing: {email['subject'][:50]}...")

        try:
            classification = classify_email(email)
            priority = classification["priority"]
            results[priority].append({**email, **classification})

            label_id = get_or_create_label(service, priority)
            apply_label(service, email["id"], label_id)

            if not SKIP_DRAFT_CREATION and classification.get("needs_reply") and priority in ("low", "normal"):
                reply_body = draft_reply(email, classification)
                sender_email = email["sender"].split("<")[-1].strip(">")
                create_draft(service, sender_email,
                             email["subject"], reply_body)
                drafts_created += 1
                print(f"   Draft reply created.")

            print(
                f"   Priority: {priority.upper()} | Category: {classification['category']}")
            print(f"   Summary: {classification['summary']}")

        except Exception as e:
            print(f"   Error processing email: {e}")
            if "Gemini quota exceeded" in str(e):
                print("\nStopping early because Gemini quota is exhausted.")
                break

    # ── Summary report ────────────────────────────────────────────────────────
    print("\n" + "="*55)
    print("  TRIAGE SUMMARY")
    print("="*55)
    print(f"  Total processed : {len(emails)}")
    print(f"  Urgent          : {len(results['urgent'])}")
    print(f"  Important       : {len(results['important'])}")
    print(f"  Normal          : {len(results['normal'])}")
    print(f"  Low priority    : {len(results['low'])}")
    print(f"  Drafts created  : {drafts_created}")
    print("="*55)

    if results["urgent"]:
        print("\n URGENT — handle these now:")
        for e in results["urgent"]:
            print(f"  - [{e['sender'].split('<')[0].strip()}] {e['subject']}")
            print(f"    Reason: {e.get('urgency_reason', '')}")

    if results["important"]:
        print("\n IMPORTANT — handle today:")
        for e in results["important"]:
            print(f"  - [{e['sender'].split('<')[0].strip()}] {e['subject']}")

    os.makedirs(REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(
        REPORTS_DIR, f"triage_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nFull report saved: {report_path}")


if __name__ == "__main__":
    run_agent()
