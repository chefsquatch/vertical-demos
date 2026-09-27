"""Shared configuration for the vertical demo assistants.

One backend serves all four verticals. Only the corpus + a short voice/goal line differ.
Onboarding a real client = replace their corpus/<key>.md file. Nothing here needs to change
unless you're adding a whole new vertical.
"""
import os
from pathlib import Path

# Load backend/.env if present so launch is one step: paste the key into .env and run.
# (On Render the key is a real env var; load_dotenv just no-ops there.)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

# Repo root = parent of this backend/ folder. Corpora live in ../corpus.
ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "corpus"

# Model — Haiku 4.5 is fast + cheap, which matters for ad-linked public traffic.
# Swap to "claude-sonnet-4-5" for a specific client who wants richer conversation.
MODEL = os.environ.get("DEMO_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = 600

# Abuse guards (these pages take raw ad traffic).
MAX_HISTORY_MESSAGES = 24          # cap conversation length sent to the model
RATE_LIMIT_PER_IP_PER_HOUR = 120   # requests per IP per rolling hour
RATE_LIMIT_BURST_PER_MIN = 12      # requests per IP per rolling minute

# CORS — where the demo pages are served from. "*" is fine for public marketing demos;
# tighten to your real domains for production.
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

# Where a captured lead is forwarded (a demo lead = a PGT sales lead, for the owner playing it).
# Set this to a URL that accepts a JSON POST — the future PGT admin backend, or an interim
# webhook (Web3Forms/Zapier/etc.). If unset, leads are only logged (no delivery). No email
# service, DB, or auth is used here on purpose — this stays a light marketing demo.
LEAD_WEBHOOK_URL = os.environ.get("LEAD_WEBHOOK_URL", "").strip()

# Each vertical: display name, brand voice, the assistant's job, and its corpus file.
VERTICALS = {
    "restaurant": {
        "name": "Nonna's Table",
        "voice": "warm, familiar, a little Italian-family charm (you can open with 'Ciao'); "
                 "refer to 'the kitchen'.",
        "goal": (
            "Take the customer's order. Capture items, quantities and any add-ons, keep a "
            "running total, sort out pickup vs delivery (honor the 4-mile radius and $20 "
            "delivery minimum), then get their name and phone so the kitchen can confirm."
        ),
        "corpus": "restaurant.md",
    },
    "salon": {
        "name": "Rowan & Sage",
        "voice": "warm, calm, unhurried and a touch elevated.",
        "goal": (
            "Book the client's request. Capture the service, preferred stylist (respect each "
            "stylist's working days) or 'first available', and preferred day/time, then their "
            "name and phone. Steer big color changes toward the free 15-minute consult."
        ),
        "corpus": "salon.md",
    },
    "auto-repair": {
        "name": "Torque Auto Care",
        "voice": "plain, straight, no-nonsense and trustworthy.",
        "goal": (
            "Book the job. Capture the vehicle's year/make/model, the symptom or service "
            "(confirm the shop actually does it), and preferred timing, then name and phone. "
            "State the $65 diagnostic fee if relevant, never a repair estimate. Flag anything "
            "unsafe to drive as urgent."
        ),
        "corpus": "auto-repair.md",
    },
    "home-services": {
        "name": "TrueFlow Home Services",
        "voice": "calm, reassuring, fast and dependable.",
        "goal": (
            "Dispatch the job. Run emergency triage first and flag urgent jobs. Capture the "
            "problem, the service address (check the service area), the urgency, then the "
            "callback number and name. State the $89 service-call fee if relevant, never a "
            "price or an ETA. For a gas smell, use the safety-first script."
        ),
        "corpus": "home-services.md",
    },
}
