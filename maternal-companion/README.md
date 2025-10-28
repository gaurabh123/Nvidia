# Maternal Companion MVP

This project bundles a Streamlit dashboard for triage and routing with a minimal FastAPI backend that wraps Twilio SMS and voice notifications.

## Prerequisites

- Python 3.10+
- Twilio account with a verified phone number or messaging service

## Setup

```bash
cd maternal-companion
python -m venv .venv
source .venv/bin/activate        # On Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

Copy `.env.example` to `.env` (or export the variables another way) and fill in your Twilio credentials:

```bash
cp .env.example .env
```

Minimal environment variables:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`

For SMS you also need either:

- `TWILIO_SMS_FROM` (an E.164 verified number), or
- `TWILIO_MESSAGING_SERVICE_SID`

For voice calls supply one of:

- `TWILIO_VOICE_CALLER_ID` (defaults to `TWILIO_SMS_FROM`), and
- `TWILIO_VOICE_TWIML_URL` (optional when sending inline TwiML).

## Streamlit app

The Streamlit UI lets you explore triage, routing, and trigger Twilio notifications from the right-hand panel.

```bash
streamlit run app.py
```

Edit `data/mothers.csv` to see how the triage table and routing respond. Use the Twilio forms to queue an SMS or voice call once credentials are set.

## FastAPI backend

The backend exposes `/notify/sms` and `/notify/voice` endpoints so other services (LLMs, task automation) can reuse the notification helpers.

```bash
uvicorn backend.api:app --reload --port 8080
```

Example request for SMS:

```bash
curl -X POST http://localhost:8080/notify/sms \
  -H "Content-Type: application/json" \
  -d '{"to": "+15551230123", "body": "Postnatal visit scheduled for 4pm."}'
```

Example request for voice (using a hosted TwiML URL):

```bash
curl -X POST http://localhost:8080/notify/voice \
  -H "Content-Type: application/json" \
  -d '{"to": "+15551230123", "twiml_url": "https://handler.twilio.com/twiml/EH123..."}'
```

Responses include the Twilio SID of the queued message or call.
