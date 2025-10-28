"""
Utility helpers for sending SMS and initiating voice calls through Twilio.

The helpers load credentials from environment variables so they can be shared
between Streamlit demos and a future FastAPI backend.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client


class TwilioConfigError(RuntimeError):
    """Raised when mandatory Twilio configuration is missing."""


@dataclass
class TwilioSettings:
    account_sid: str
    auth_token: str
    sms_from: Optional[str] = None
    messaging_service_sid: Optional[str] = None
    voice_caller_id: Optional[str] = None
    voice_twiml_url: Optional[str] = None


def load_settings() -> TwilioSettings:
    """
    Load Twilio credentials from environment variables.

    Required:
        TWILIO_ACCOUNT_SID
        TWILIO_AUTH_TOKEN
    Optional:
        TWILIO_SMS_FROM               (E.164 sender number)
        TWILIO_MESSAGING_SERVICE_SID  (alternative to TWILIO_SMS_FROM)
        TWILIO_VOICE_CALLER_ID        (outbound caller id for voice)
        TWILIO_VOICE_TWIML_URL        (URL with TwiML instructions)
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        raise TwilioConfigError("Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN environment variables.")

    return TwilioSettings(
        account_sid=account_sid,
        auth_token=auth_token,
        sms_from=os.getenv("TWILIO_SMS_FROM"),
        messaging_service_sid=os.getenv("TWILIO_MESSAGING_SERVICE_SID"),
        voice_caller_id=os.getenv("TWILIO_VOICE_CALLER_ID"),
        voice_twiml_url=os.getenv("TWILIO_VOICE_TWIML_URL"),
    )


def get_client(settings: Optional[TwilioSettings] = None) -> Client:
    """Instantiate a Twilio REST client."""
    settings = settings or load_settings()
    return Client(settings.account_sid, settings.auth_token)


def send_sms(
    to: str,
    body: str,
    *,
    client: Optional[Client] = None,
    settings: Optional[TwilioSettings] = None,
    from_number: Optional[str] = None,
    messaging_service_sid: Optional[str] = None,
) -> str:
    """
    Send an SMS message and return the Twilio message SID.

    Provide either a Messaging Service SID or a from_number (E.164).
    The function falls back to values loaded from settings when omitted.
    """
    settings = settings or load_settings()
    client = client or get_client(settings)
    messaging_service_sid = messaging_service_sid or settings.messaging_service_sid
    from_number = from_number or settings.sms_from

    if not messaging_service_sid and not from_number:
        raise TwilioConfigError("Provide TWILIO_MESSAGING_SERVICE_SID or TWILIO_SMS_FROM to send SMS.")

    try:
        message = client.messages.create(
            to=to,
            body=body,
            from_=from_number,
            messaging_service_sid=messaging_service_sid,
        )
    except TwilioRestException as exc:
        raise RuntimeError(f"Failed to send SMS via Twilio: {exc.msg}") from exc

    return message.sid


def initiate_call(
    to: str,
    *,
    client: Optional[Client] = None,
    settings: Optional[TwilioSettings] = None,
    from_number: Optional[str] = None,
    twiml_url: Optional[str] = None,
    twiml: Optional[str] = None,
) -> str:
    """
    Initiate an outbound voice call.

    Provide either a publicly reachable twiml_url or raw twiml instructions.
    """
    settings = settings or load_settings()
    client = client or get_client(settings)

    from_number = from_number or settings.voice_caller_id or settings.sms_from
    if not from_number:
        raise TwilioConfigError("Set TWILIO_VOICE_CALLER_ID or TWILIO_SMS_FROM for outbound calls.")

    twiml_url = twiml_url or settings.voice_twiml_url
    if not twiml_url and not twiml:
        raise TwilioConfigError("Provide TWILIO_VOICE_TWIML_URL or twiml content for voice calls.")

    create_kwargs = {"to": to, "from_": from_number}
    if twiml:
        create_kwargs["twiml"] = twiml
    else:
        create_kwargs["url"] = twiml_url

    try:
        call = client.calls.create(**create_kwargs)
    except TwilioRestException as exc:
        raise RuntimeError(f"Failed to initiate voice call via Twilio: {exc.msg}") from exc

    return call.sid
