"""
FastAPI service exposing Twilio-backed messaging helpers.

Run with:
    uvicorn backend.api:app --reload --port 8080
"""

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from .twilio_service import (
    TwilioConfigError,
    initiate_call,
    send_sms,
)


class SMSRequest(BaseModel):
    to: str
    body: str
    from_number: str | None = None
    messaging_service_sid: str | None = None


class SMSResponse(BaseModel):
    sid: str


class VoiceRequest(BaseModel):
    to: str
    from_number: str | None = None
    twiml_url: str | None = None
    twiml: str | None = None


class VoiceResponse(BaseModel):
    sid: str


app = FastAPI(title="Maternal Companion Backend", version="0.1.0")


@app.get("/healthz")
def healthcheck():
    return {"status": "ok"}


@app.post("/notify/sms", response_model=SMSResponse, status_code=status.HTTP_202_ACCEPTED)
def notify_sms(payload: SMSRequest):
    try:
        sid = send_sms(
            to=payload.to,
            body=payload.body,
            from_number=payload.from_number,
            messaging_service_sid=payload.messaging_service_sid,
        )
    except TwilioConfigError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return SMSResponse(sid=sid)


@app.post("/notify/voice", response_model=VoiceResponse, status_code=status.HTTP_202_ACCEPTED)
def notify_voice(payload: VoiceRequest):
    try:
        sid = initiate_call(
            to=payload.to,
            from_number=payload.from_number,
            twiml_url=payload.twiml_url,
            twiml=payload.twiml,
        )
    except TwilioConfigError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return VoiceResponse(sid=sid)
