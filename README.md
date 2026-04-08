# Candidate Evaluator — Plum Builder's Residency

An AI agent that evaluates job applications over email. Candidates send their resume, GitHub profile, and portfolio link. The agent extracts signals, scores them against a rubric, and replies with a pass or fail decision — automatically.

---

## Architecture

Inbound emails hit a Postmark inbound address, which fires a webhook to the FastAPI server (hosted on Railway). The server parses the email, extracts the PDF resume (pdfplumber), fetches GitHub signals (GitHub REST API), and scrapes the portfolio URL (httpx + BeautifulSoup) — all in parallel. Those signals are passed to Claude (claude-sonnet-4-6) which scores the candidate across five rubric dimensions and returns structured JSON. The server then sends a pass or fail email via Postmark outbound and logs the result to SQLite.

```
Candidate email
      ↓
Postmark Inbound Webhook
      ↓
FastAPI (Railway)
      ↓
Parallel extraction:
  PDF → pdfplumber → resume text
  GitHub URL → GitHub API → signals
  Portfolio URL → httpx → page content
      ↓
Claude API → structured JSON evaluation
      ↓
Postmark Outbound → reply to candidate
      ↓
SQLite → log result
```

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python | Best LLM ecosystem, fast to build |
| Web framework | FastAPI | Lightweight, async, ideal for webhooks |
| Email (inbound) | Postmark Inbound | Parses emails into JSON webhooks — no IMAP polling needed |
| Email (outbound) | Postmark SMTP | Same platform, reliable deliverability |
| PDF parsing | pdfplumber | Best text extraction for text-based PDFs |
| GitHub signals | GitHub REST API | Official API, no scraping fragility |
| Portfolio scraping | httpx + BeautifulSoup | Lightweight, good enough for signal extraction |
| AI evaluation | Claude claude-sonnet-4-6 | Best reasoning quality for nuanced builder evaluation |
| Deployment | Railway | One-command deploy, always-on, env var management |
| Storage | SQLite | Zero-config, sufficient for this scale |

---

## Evaluation Rubric

| Dimension | Weight |
|---|---|
| Shipped production products | 30% |
| Technical depth | 25% |
| Business thinking | 20% |
| Speed of execution | 15% |
| Communication clarity | 10% |

Pass threshold: **65/100** (configurable via `PASS_THRESHOLD` env var).

---

## Setup

### 1. Clone and install

```bash
git clone <repo-url>
cd candidate-evaluator
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in your keys
```

### 3. Postmark setup

1. Create a free account at [postmarkapp.com](https://postmarkapp.com)
2. Create a Server → go to **Inbound** tab → copy your inbound email address
3. Set the inbound webhook URL to: `https://your-railway-app.up.railway.app/webhook/inbound`
4. Verify a sender email for outbound (Settings → Sender Signatures)
5. Copy the Server API Token → set as `POSTMARK_SERVER_TOKEN`

### 4. Run locally

```bash
uvicorn main:app --reload --port 8000
```

### 5. Test locally

```bash
# Without a PDF (tests edge cases only)
python tests/test_webhook.py

# With a real PDF resume
python tests/test_webhook.py /path/to/resume.pdf
```

---

## Deployment (Railway)

```bash
# Install Railway CLI
npm install -g @railway/cli

# Deploy
railway login
railway init
railway up

# Set environment variables
railway variables set POSTMARK_SERVER_TOKEN=...
railway variables set POSTMARK_FROM_EMAIL=...
railway variables set ANTHROPIC_API_KEY=...
railway variables set GITHUB_TOKEN=...
```

Once deployed, update your Postmark inbound webhook URL to the Railway domain.

---

## Edge Cases Handled

| Scenario | Behavior |
|---|---|
| No resume attached | Replies asking for PDF resume |
| No GitHub link | Replies asking for GitHub URL |
| Scanned/image PDF | Replies asking for text-based PDF |
| Invalid/missing GitHub profile | Notes it, evaluates on remaining signals |
| Private GitHub (no public repos) | Scores low on technical dimension, noted in evaluation |
| Portfolio URL inaccessible | Notes it, evaluates on remaining signals |
| Duplicate application | Replies: "We already have your application on file" |
| Non-application email | Replies explaining this inbox is for applications only |

---

## Admin

View all processed applications:
```
GET /applications
```

---

## What I'd Improve With More Time

1. **Postmark webhook signature verification** — currently skipped. Should validate the `X-Postmark-Signature` header to prevent spoofed webhooks.

2. **GitHub GraphQL for pinned repos** — the REST API doesn't expose pinned repos. GraphQL would give richer signal on what the candidate considers their best work.

3. **Smarter portfolio parsing** — currently basic text extraction. Could do deeper parsing for specific platforms (Notion, Behance, Product Hunt) to extract structured project data.

4. **Re-application window** — currently one application per email address forever. A 90-day cooldown would be more humane.

5. **Admin dashboard** — a simple UI to review evaluations, override decisions, and see scoring breakdowns.

6. **Retry logic** — if Claude or GitHub API is temporarily down, silently fails. Should queue and retry.

---

## Trade-offs Made Consciously

- **SQLite over Postgres** — sufficient for this scale, zero setup. Would swap for Postgres in production.
- **No webhook signature verification** — saved time, documented as known gap.
- **Best-effort portfolio scraping** — some sites block scrapers. Chose to not fail the application, just note it in evaluation.
- **Background task over queue** — FastAPI's `BackgroundTasks` works for low volume. Would use Celery + Redis at scale.
- **Weighted total recomputed server-side** — don't trust Claude's math. Always recompute from scores + weights.
