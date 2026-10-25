"""Shared FastAPI backend for the four vertical demo assistants.

POST /chat   {vertical, messages:[{role,content}]}  ->  {reply, lead|null}  (a lead is emailed to PGT)
POST /event  {vertical, event}                       ->  usage counter (view, chat_open, ...)
GET  /stats                                          ->  counter totals since last restart
GET  /health (also HEAD)                             ->  keep-warm ping
GET  /verticals                                      ->  list of configured verticals

Run locally:  uvicorn app:app --reload --port 8000
"""
import json
import os
import re
import time
from collections import defaultdict, deque

import anthropic
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import (
    ALLOWED_ORIGINS, EMAIL_FROM, LEAD_EMAIL_TO, LEAD_WEBHOOK_URL, MAX_HISTORY_MESSAGES,
    MAX_TOKENS, MODEL, RATE_LIMIT_BURST_PER_MIN, RATE_LIMIT_PER_IP_PER_HOUR, RESEND_API_KEY,
    TRACKED_EVENTS, VERTICALS,
)
from prompts import build_system_prompt

# The trailing newline on a pasted key breaks the SDK ("Connection error") — strip it.
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
client = anthropic.Anthropic(api_key=API_KEY)

app = FastAPI(title="PGT Vertical Demo Assistants")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

LEAD_RE = re.compile(r"<<LEAD>>(.*?)<<END>>", re.DOTALL)
_hits = defaultdict(deque)  # ip -> deque[timestamps]
# Usage counter since this process started: {"YYYY-MM-DD": {"salon": {"view": 3, ...}}}.
# Every event is also logged as "[EVENT] <vertical> <event>", so Render's log search keeps the
# full history across restarts.
_counts = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))


class ChatIn(BaseModel):
    vertical: str
    messages: list  # [{"role": "user"|"assistant", "content": "..."}]


def _client_ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else "?")


def _rate_limited(ip: str) -> bool:
    now = time.time()
    dq = _hits[ip]
    while dq and dq[0] < now - 3600:
        dq.popleft()
    if len(dq) >= RATE_LIMIT_PER_IP_PER_HOUR:
        return True
    if sum(1 for t in dq if t > now - 60) >= RATE_LIMIT_BURST_PER_MIN:
        return True
    dq.append(now)
    return False


def _lead_email_text(vertical: str, lead: dict, msgs: list) -> str:
    name = VERTICALS[vertical]["name"]
    lines = [
        f"New lead from the {name} demo ({vertical}).",
        "",
        f"Name:    {lead.get('name', '')}",
        f"Phone:   {lead.get('phone', '')}",
        f"Wants:   {lead.get('summary', '')}",
        f"Urgent:  {'YES' if lead.get('urgent') else 'no'}",
        "",
        "This came from a demo page, so it may be a business owner trying it out.",
        "",
        "Conversation:",
    ]
    for m in msgs:
        who = "Visitor" if m["role"] == "user" else name
        lines.append(f"{who}: {LEAD_RE.sub('', m['content']).strip()}")
    return "\n".join(lines)


async def _email_lead(vertical: str, lead: dict, msgs: list) -> None:
    """Email the lead + transcript to PGT. Never raises: a failed send must not break the chat."""
    if not RESEND_API_KEY:
        return
    subject = f"Demo lead: {lead.get('name') or 'someone'} via {VERTICALS[vertical]['name']}"
    payload = {
        "from": EMAIL_FROM,
        "to": [LEAD_EMAIL_TO],
        "subject": subject,
        "text": _lead_email_text(vertical, lead, msgs),
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as http:
            r = await http.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json=payload,
            )
        if r.status_code >= 300:
            print(f"[LEAD-EMAIL-FAILED] {vertical} {r.status_code} {r.text[:300]}", flush=True)
        else:
            print(f"[LEAD-EMAILED] {vertical} -> {LEAD_EMAIL_TO}", flush=True)
    except Exception as e:
        print(f"[LEAD-EMAIL-FAILED] {vertical} {type(e).__name__}: {e}", flush=True)


def _count(vertical: str, event: str) -> None:
    _counts[time.strftime("%Y-%m-%d")][vertical][event] += 1
    print(f"[EVENT] {vertical} {event}", flush=True)


async def _forward_lead(vertical: str, lead: dict) -> None:
    """Forward a captured lead to PGT (the demo's whole point: it's a sales lead for the owner).

    Always logs it. If LEAD_WEBHOOK_URL is set, also POSTs it there (the future PGT admin
    backend, or an interim webhook). A delivery failure is swallowed and logged — it must
    never break the live demo.
    """
    print(f"[LEAD] {vertical} {json.dumps(lead)}", flush=True)
    if not LEAD_WEBHOOK_URL:
        return
    payload = {"source": "vertical-demos", "vertical": vertical, "lead": lead}
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            await http.post(LEAD_WEBHOOK_URL, json=payload)
    except Exception as e:  # never let lead delivery take down the chat
        print(f"[LEAD-FORWARD-FAILED] {vertical} {type(e).__name__}: {e}", flush=True)


def _no_em_dashes(text: str) -> str:
    """Founder rule (2026-10-24): no em dashes in customer-facing text; they read as AI-written."""
    return re.sub(r"\s*—\s*", ", ", text)


def _extract_lead(text: str):
    """Pull the lead JSON out of the reply and return (visible_text, lead|None)."""
    text = _no_em_dashes(text)
    m = LEAD_RE.search(text)
    if not m:
        return text.strip(), None
    visible = LEAD_RE.sub("", text).strip()
    try:
        lead = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        lead = None
    return visible, lead


@app.get("/health")
@app.head("/health")
def health():
    return {"ok": True, "model": MODEL, "key_loaded": bool(API_KEY)}


@app.get("/verticals")
def verticals():
    return {k: {"name": v["name"]} for k, v in VERTICALS.items()}


@app.post("/event")
async def event(request: Request):
    """Demo usage counter. Body is JSON {vertical, event}; sent with navigator.sendBeacon as
    text/plain (no CORS preflight), so parse the raw body ourselves."""
    try:
        data = json.loads((await request.body()) or b"{}")
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"error": "bad body"}, status_code=400)
    vertical, ev = str(data.get("vertical", "")), str(data.get("event", ""))
    if vertical not in VERTICALS or ev not in TRACKED_EVENTS:
        return JSONResponse({"error": "unknown vertical or event"}, status_code=400)
    if _rate_limited(_client_ip(request)):
        return JSONResponse({"error": "slow down"}, status_code=429)
    _count(vertical, ev)
    return {"ok": True}


@app.get("/stats")
def stats():
    """Counts since the last restart, by day. Full history: search Render logs for [EVENT]."""
    return {day: {v: dict(evs) for v, evs in by_v.items()} for day, by_v in _counts.items()}


@app.post("/chat")
async def chat(body: ChatIn, request: Request):
    if body.vertical not in VERTICALS:
        return JSONResponse({"error": "unknown vertical"}, status_code=400)
    if not API_KEY:
        return JSONResponse({"error": "server missing API key"}, status_code=500)

    ip = _client_ip(request)
    if _rate_limited(ip):
        return JSONResponse(
            {"error": "Slow down a moment — too many messages. Try again shortly."},
            status_code=429,
        )

    # Keep only well-formed turns, cap length.
    msgs = [
        {"role": m["role"], "content": str(m["content"])[:4000]}
        for m in body.messages
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ][-MAX_HISTORY_MESSAGES:]
    # The Anthropic API requires the first turn to be the user's. Clients seed a client-side
    # greeting as an assistant turn, so drop any leading assistant turns here too.
    while msgs and msgs[0]["role"] == "assistant":
        msgs.pop(0)
    if not msgs:
        return JSONResponse({"error": "no messages"}, status_code=400)

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=build_system_prompt(body.vertical),
            messages=msgs,
        )
        raw = "".join(b.text for b in resp.content if b.type == "text")
    except Exception as e:  # keep the demo alive; the front end still captures the lead
        print(f"[error] {body.vertical} {type(e).__name__}: {e}", flush=True)
        return JSONResponse(
            {"error": "The assistant is briefly unavailable — leave your name and number and "
                      "we'll reach you."},
            status_code=502,
        )

    reply, lead = _extract_lead(raw)
    if lead:
        _count(body.vertical, "lead")
        await _forward_lead(body.vertical, lead)
        await _email_lead(body.vertical, lead, msgs + [{"role": "assistant", "content": reply}])

    return {"reply": reply, "lead": lead}
