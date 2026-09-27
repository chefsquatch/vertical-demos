"""Shared FastAPI backend for the four vertical demo assistants.

POST /chat   {vertical, messages:[{role,content}]}  ->  {reply, lead|null}
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
    ALLOWED_ORIGINS, LEAD_WEBHOOK_URL, MAX_HISTORY_MESSAGES, MAX_TOKENS, MODEL,
    RATE_LIMIT_BURST_PER_MIN, RATE_LIMIT_PER_IP_PER_HOUR, VERTICALS,
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


def _extract_lead(text: str):
    """Pull the lead JSON out of the reply and return (visible_text, lead|None)."""
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
        await _forward_lead(body.vertical, lead)

    return {"reply": reply, "lead": lead}
