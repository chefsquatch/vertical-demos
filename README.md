# Vertical Demo Assistants

Four industry chatbot demos — **restaurant, salon, auto-repair, home-services** — each a
polished landing page with a **real, grounded Claude assistant** you can link to from ads or
drop on a website. Same engine for all four; only the corpus and brand voice change.

## Live

- **Backend (shared):** https://vertical-demos.onrender.com — Render free tier, model
  `claude-haiku-4-5`. Endpoints: `POST /chat`, `GET /health`, `GET /verticals`.
- **Auto-deploy:** pushes to `master` deploy automatically (Render is connected to this repo via
  the GitHub app). The four `web/*.html` pages point at the backend's `/chat`.

```
vertical-demos/
  corpus/                  <- the knowledge each assistant answers from (edit these per client)
    restaurant.md
    salon.md
    auto-repair.md
    home-services.md
  BEHAVIOR.md              <- how the assistants behave (the "how they work" spec)
  backend/                 <- one shared FastAPI service, real Claude, grounded on the corpus
    app.py  config.py  prompts.py  requirements.txt  render.yaml  .env.example
  web/                     <- the four demo pages, wired to the backend
    demo-restaurant.html  demo-salon.html  demo-auto-repair.html  demo-home-services.html
```

## How it works (the short version)

Each page has a real chat box. When a visitor types, the page POSTs the conversation to the
shared backend with a `vertical` tag. The backend injects that vertical's **corpus** into the
system prompt and calls Claude. Three rules are enforced (full detail in `BEHAVIOR.md`):

1. **Grounded** — answers only from the corpus.
2. **Honest or quiet** — if it's not in the corpus, it says so and hands off; it never invents
   a price, time, ETA, availability, or a service the business doesn't offer.
3. **Capture the lead** — every real conversation ends with name + phone, and the backend logs
   the captured lead.

Failsafe: if the backend ever errors, the page still asks for name + number so no lead is lost.

## Run it locally

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate      # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env        # then paste your ANTHROPIC_API_KEY into .env (no trailing newline)
# load .env into the shell, or set the var directly, then:
uvicorn app:app --reload --port 8000
```

Then open `web/demo-restaurant.html` in a browser. For local testing, change the `api` line at
the top of the page's script to `http://localhost:8000/chat`.

Quick backend check: `curl http://localhost:8000/health`

## Tests

Contract tests (Claude call mocked, so no key needed) cover both the guard/refusal paths and
the success paths — grounding per vertical, no cross-corpus leakage, lead capture, history
sanitising, unknown-vertical/empty/missing-key/rate-limit/upstream-error handling.

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate
pip install -r requirements.txt pytest          # (py3.14 locally: install unpinned; Render uses 3.12)
pytest -q
```

The one thing tests can't prove is live generation quality — run one real chat after inserting
the key. The front-end flow (send, reply, lead card, run-again, and the backend-down failsafe)
was verified in-browser against a mock backend.

## Deploy the backend (Render)

1. Push this folder to a repo (e.g. `chefsquatch/vertical-demos`).
2. In Render: **New → Blueprint**, point at the repo (it reads `backend/render.yaml`).
   - Make sure the Render GitHub App has access to the repo, or auto-deploy won't fire.
3. Set `ANTHROPIC_API_KEY` in the Render dashboard (it's `sync:false`, never committed).
4. After it's live, note the URL, e.g. `https://vertical-demos.onrender.com`.
5. Keep-warm (free tier sleeps): point UptimeRobot at `/health` (it answers HEAD).

## Point the pages at the live backend

Each page has this near the top of its `<script>`:

```js
api:"https://vertical-demos.onrender.com/chat",
```

Set it to your real Render URL **+ `/chat`** in all four `web/*.html` files. Then host the
pages anywhere static (Vercel, GitHub Pages, Netlify) and link ads to them.

## Onboarding a real client

1. Copy the matching `corpus/<vertical>.md` and replace the placeholder data with the client's
   real menu / services / hours / prices / area. **The corpus is the only thing that changes
   the answers** — nothing in the code needs editing.
2. Swap the brand name, colors, and copy in the client's `web/*.html`.
3. (Optional) A brand-new vertical: add an entry to `VERTICALS` in `backend/config.py` and a new
   `corpus/<key>.md`.

## Cost

Default model is **Haiku 4.5** — fast and cheap, which matters because these take public ad
traffic. Abuse guards in `config.py` cap requests per IP and conversation length. For a client
who wants richer conversation, set `DEMO_MODEL=claude-sonnet-4-5`.

## Production TODO (wiring the lead to the owner)

Right now a captured lead is logged server-side (`[LEAD] ...`). To actually deliver it, add an
SMS (Twilio) or email (Resend) send at the marked spot in `app.py` (`# Production: dispatch...`).
