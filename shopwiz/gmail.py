"""Reads promotional emails from Gmail (read-only) so ShopWiz can find coupons and personal offers.

Sign-in uses Google OAuth. The access token lives only in an http-only browser cookie for up to an hour, and
email text is parsed in memory and thrown away. Nothing from the inbox is saved on the server.

Setup: create an OAuth client (Web application) in Google Cloud Console, enable the Gmail API, add
http://localhost:8000/auth/google/callback as a redirect URI, and set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.
"""

import base64
import html
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import requests

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
# Promotions from the last 30 days that mention a discount. Gmail does the filtering, so we only download matches.
QUERY = 'category:promotions newer_than:30d ("% off" OR "$ off" OR code OR coupon OR reward OR "for you")'
MAX_MESSAGES = 40


def configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def auth_url(redirect_uri: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "online", "include_granted_scopes": "true", "prompt": "consent", "state": state,
    })


def exchange(code: str, redirect_uri: str) -> dict:
    r = requests.post(TOKEN_URL, timeout=15, data={
        "code": code, "client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    r.raise_for_status()
    return r.json()


def revoke(token: str) -> None:
    try:
        requests.post(REVOKE_URL, params={"token": token}, timeout=10)
    except requests.RequestException:
        pass


def promo_messages(token: str) -> list[dict]:
    """Recent promotional emails as {id, sender, sender_email, subject, body, date}."""
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/messages", params={"q": QUERY, "maxResults": MAX_MESSAGES}, headers=headers, timeout=15)
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("messages", [])]

    def fetch(mid):
        resp = requests.get(f"{API}/messages/{mid}", params={"format": "full"}, headers=headers, timeout=15)
        return _simplify(resp.json()) if resp.ok else None

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [m for m in pool.map(fetch, ids) if m]


def _simplify(msg: dict) -> dict:
    payload = msg.get("payload", {})
    h = {x["name"].lower(): x["value"] for x in payload.get("headers", [])}
    sender = h.get("from", "")
    email = re.search(r"<([^>]+)>", sender)
    return {"id": msg.get("id"), "sender": sender, "sender_email": email.group(1) if email else sender,
            "subject": h.get("subject", ""), "date": h.get("date", ""), "body": _body(payload) or msg.get("snippet", "")}


def _body(part: dict) -> str:
    """Plain text of an email, preferring the text part and falling back to HTML with tags removed."""
    plain, rich = [], []

    def walk(p):
        data = p.get("body", {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "ignore")
            (plain if p.get("mimeType") == "text/plain" else rich if p.get("mimeType") == "text/html" else []).append(text)
        for child in p.get("parts", []) or []:
            walk(child)

    walk(part)
    if plain:
        return "\n".join(plain)
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", "\n".join(rich))
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))
