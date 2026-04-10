# Candidate Evaluator — Plum Builder's Residency

An AI agent that evaluates job applications over email. Candidates send their resume, GitHub profile, and portfolio link to an inbound email address. The agent extracts signals, scores them against a rubric, and replies with a pass or fail decision — automatically, within 60 seconds.

---

## Architecture

Inbound emails arrive at a Postmark inbound address, which converts them into a JSON webhook and fires it at the FastAPI server hosted on Render. The server immediately returns `200` and hands off to a background task — so Postmark never times out regardless of how long evaluation takes. The background task runs three extractions in parallel: the resume is parsed via pdfplumber (text-based PDFs) or Claude Vision (scanned PDFs, images, DOCX); GitHub signals are pulled from the GitHub REST API; and the portfolio URL is scraped via httpx + BeautifulSoup. All three signal sets are passed to Claude (claude-sonnet-4-6), which scores the candidate across five rubric dimensions and returns structured JSON. The server saves the result to SQLite, then sends the reply via Brevo's transactional email API. If the email fails, the evaluation is already saved — they're decoupled.

```
Candidate email
      ↓
Postmark Inbound → webhook (JSON)
      ↓
FastAPI on Render (returns 200 immediately)
      ↓
Background task — parallel extraction:
  ├── Resume (PDF/DOCX/image) → pdfplumber → text
  │                           → Claude Vision (if scanned/image)
  ├── GitHub URL → GitHub REST API → signals (repos, stars, followers, languages)
  └── Portfolio URL → httpx + BeautifulSoup → page content
      ↓
Claude claude-sonnet-4-6 → structured JSON evaluation
      ↓
SQLite → save result        Brevo → reply to candidate
```

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.9 | Best LLM ecosystem, fastest to build with |
| Web framework | FastAPI | Async-native, background tasks built-in, minimal boilerplate |
| Email inbound | Postmark | Converts raw SMTP to clean JSON webhooks — no IMAP polling, no parsing headaches |
| Email outbound | Brevo | Transactional email with Gmail sender verification; Postmark restricts public domains for outbound |
| PDF parsing | pdfplumber | Best-in-class text extraction for text-based PDFs, pure Python |
| Scanned PDF / image resume | Claude Vision | Zero additional dependencies vs. tesseract/poppler; handles DOCX, images, scanned PDFs in one call |
| GitHub signals | GitHub REST API | Official API, stable, no scraping fragility; fetches top 100 repos by stars for accurate signal |
| Portfolio scraping | httpx + BeautifulSoup | Lightweight; sufficient for extracting text signal — not trying to render JS |
| AI evaluation | Claude claude-sonnet-4-6 | Best reasoning quality for nuanced judgment; structured JSON output with retry-safe extraction |
| Deployment | Render | Free tier with auto-deploy from GitHub push; zero config |
| Storage | SQLite via aiosqlite | Zero setup, sufficient for this scale, async-compatible |

---

## Evaluation Rubric

The agent scores candidates across five dimensions, weighted by importance:

| Dimension | Weight | What it looks for |
|---|---|---|
| Shipped production products | 30% | Real products with real users — professional launches (resume), OSS libraries, live apps, App Store listings |
| Technical depth | 25% | Range of languages, repo quality, complexity of work, GitHub activity at scale |
| Business thinking | 20% | Metrics, user outcomes, understanding of why they built what they built |
| Speed of execution | 15% | Recent shipping — job history, GitHub activity in last 90 days |
| Communication clarity | 10% | Clear resume, specific project descriptions, confident writing |

Pass threshold: **65/100** (configurable via `PASS_THRESHOLD` env var).

**Calibration decisions made:**
- Resume work experience is treated as equal signal to GitHub — the brief explicitly lists both. A PM-builder with ₹4Cr revenue products on their resume shouldn't fail because they don't have personal GitHub repos.
- Hard score floors are applied in code (not in Claude's judgment) for accounts with 10,000+ followers AND 100+ repos — peer-validated builder signals that can't be gamed by prompt reasoning.
- Accounts with high followers but very few repos (< 15) are flagged as likely tutorial/mascot accounts and scored on repo quality only.

---

## Edge Cases Handled

| Scenario | Behaviour |
|---|---|
| Missing resume | Asks for it; saves as `incomplete` so they can resubmit |
| Missing GitHub link | Asks for it; allows resubmission |
| Scanned / image PDF | Falls back to Claude Vision automatically |
| DOCX or Word document | Parsed via python-docx |
| Image of resume (PNG/JPG) | Parsed via Claude Vision |
| Google Drive / Dropbox link instead of attachment | Tries to fetch if public; if gated, gives specific error explaining to attach directly |
| GitHub repo URL instead of profile URL | Extracts username from repo path correctly |
| GitHub URL with trailing punctuation | Stripped correctly before lookup |
| HTML-only email (no plain text) | Extracts href URLs before stripping tags |
| Invalid / non-existent GitHub profile | Notes the error, evaluates on remaining signals |
| Duplicate application (already pass/fail) | Blocked with a polite reply |
| Incomplete → resubmit | Allowed; only pass/fail blocks resubmission |
| Non-application email | Replies explaining this inbox is for applications only |
| Claude returns malformed JSON | Extracts JSON from response defensively; replies asking to resubmit on failure |
| Email delivery failure | Evaluation saved to DB regardless; email failure logged separately |

---

## What I'd Improve With More Time

**1. GitHub GraphQL for richer signals**
The REST API doesn't expose pinned repos — what a candidate chooses to highlight is a strong signal. GraphQL would also give contribution graphs and organisation membership, which matters for engineers whose best work is in private company repos.

**2. Smarter portfolio parsing**
Current implementation does basic text extraction. Many strong candidates link to Notion pages, Product Hunt launches, or App Store listings — structured parsers for these platforms would extract far better signal than raw text scraping.

**3. Re-application window instead of permanent block**
Right now a pass/fail decision blocks resubmission forever. A 90-day cooldown is more humane and reflects how real hiring works — people improve.

**4. Webhook signature verification**
Postmark signs webhooks with an HMAC header. Currently not verified — any POST to `/webhook/inbound` is processed. Low risk at this scale, but a production system should validate it.

**5. Persistent storage**
SQLite on Render's free tier is ephemeral — data is lost on restart. A hosted Postgres (Supabase free tier, or Render's managed Postgres) would make this production-grade without meaningful cost.

**6. Async Claude client**
The evaluator currently uses the sync `anthropic.Anthropic` client inside an async FastAPI route. It works (background tasks run in a thread pool), but the async client would be cleaner and avoid potential blocking.

---

## Trade-offs Made Consciously

**SQLite over Postgres**
Zero setup, zero cost, works for this volume. Ephemeral on Render free tier — documented as a known gap, acceptable for a 5-day build.

**Background tasks over a job queue**
FastAPI's `BackgroundTasks` is sufficient for low volume and has zero infrastructure overhead. At scale, Celery + Redis or a proper queue would be the right call.

**pdfplumber → Claude Vision fallback (not OCR)**
tesseract + poppler requires system binaries that are painful to install on managed hosting. Claude Vision handles scanned PDFs, images, and DOCX in one unified path with no system dependencies — simpler, more robust, slightly higher token cost per eval.

**Score floors in code, not in prompts**
Early versions relied purely on prompt instructions to score prolific OSS authors correctly. Claude's reasoning about name mismatches and its "consumer app" bias made this inconsistent. Hard floors based on verifiable signals (followers + repo count) applied in Python are deterministic and can't be overridden by prompt reasoning.

**Resume work experience = valid as GitHub signals**
The brief explicitly lists "work experience" as a signal equal to GitHub activity. A PM-builder with professional product launches documented on their resume shouldn't be penalised for low personal GitHub activity. Trade-off: resume claims are unverifiable, GitHub is ground truth. Mitigated by the `technical_ownership` field which explicitly flags whether the candidate wrote code personally.

**Brevo over Postmark for outbound**
Postmark doesn't allow personal Gmail addresses as verified sender signatures. Brevo allows individual email verification and has a generous free tier.

---

## Local Setup

```bash
git clone https://github.com/vishvesh245/candidate-evaluator-agent.git
cd candidate-evaluator-agent
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in your keys
uvicorn main:app --reload --port 8000
```

**Required env vars:**
```
POSTMARK_SERVER_TOKEN=
BREVO_API_KEY=
ANTHROPIC_API_KEY=
GITHUB_TOKEN=
PASS_THRESHOLD=65          # optional, default 65
TEST_EMAIL_OVERRIDE=       # optional: route all emails to one address for testing
```

## Running Tests

```bash
python tests/run_eval_tests.py /path/to/resume.pdf
```

Runs 13 test cases: E1–E7 (edge cases), Q1–Q5 (evaluation quality), S1 (resilience). All tests hit the local server at `localhost:8000`. Switch `BASE_URL` in the script to test against the live deployment.

## Admin Dashboard

```
GET /applications        → HTML dashboard with scores, signals, response times
GET /applications/data   → JSON (programmatic access)
```
