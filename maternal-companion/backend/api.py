import html
import logging
import re
from typing import Dict, List, Optional

from fastapi import FastAPI, Form
from fastapi.responses import Response

from .nvidia_client import NvidiaConfigError, NvidiaGenerationError, get_vim_client


app = FastAPI(title="Maternal Companion Voice Backend", version="0.6.0")
logger = logging.getLogger(__name__)

_call_sessions: Dict[str, List[Dict[str, str]]] = {}

ASSISTANT_SYSTEM_PROMPT = (
    "You are \"AmaSathi\", a compassionate maternal and newborn health assistant. "
    "Your expertise is limited to pregnancy, postpartum recovery, newborn care, family planning, and related maternal-health topics. "
    "If a request falls outside these topics (for example, banking, sports, technology, or other unrelated subjects), respond with a brief apology and explain that you can only help with maternal and newborn health questions. "
    "Never expose internal reasoning, deliberation, or step-by-step thoughts—only share the final answer. "
    "Always answer in clear, supportive English using at most two short sentences. "
    "Focus on the single most important next action or two; do not enumerate long lists, do not repeat yourself, and do not describe your thought process. "
    "End with a brief invitation in English for the caller to ask another maternal-health question or say goodbye."
)

chat_client = get_vim_client()


@app.get("/healthz")
def healthcheck():
    return {"status": "ok"}


def _build_gather_twiml(prompt: str) -> str:
    """Ask the caller a question and wait for spoken input."""
    escaped = html.escape(prompt or "")
    parts = [
        "<Response>",
        '<Gather input="speech" language="en-US" speechTimeout="auto" action="/twilio/voice/process" method="POST">',
        f"<Say>{escaped}</Say>",
        "</Gather>",
        "<Say>I didn't catch that. Goodbye.</Say>",
        "<Hangup/>",
        "</Response>",
    ]
    return "".join(parts)


def _build_reply_twiml(reply: str, *, end_call: bool = False) -> str:
    escaped = html.escape(reply or "")
    parts = ["<Response>", f"<Say>{escaped}</Say>"]
    if end_call:
        parts.append("<Say>Goodbye.</Say>")
        parts.append("<Hangup/>")
    else:
        parts.append(
            '<Gather input="speech" language="en-US" speechTimeout="auto" action="/twilio/voice/process" method="POST">'
            "<Say>You can ask another question or say goodbye.</Say>"
            "</Gather>"
        )
        parts.append("<Say>I didn't catch that. Goodbye.</Say>")
        parts.append("<Hangup/>")
    parts.append("</Response>")
    return "".join(parts)


def _should_end_conversation(text: str) -> bool:
    lowered = (text or "").strip().lower()
    end_keywords = {"goodbye", "bye", "thanks", "thank you", "stop", "exit", "hang up"}
    return any(keyword in lowered for keyword in end_keywords)


def _condense_reply(text: str) -> str:
    """Strip hidden reasoning tags and keep at most two concise sentences."""
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    condensed = " ".join(sentences[:2])
    return condensed.strip()


@app.post("/twilio/voice", response_class=Response)
def handle_twilio_voice(
    call_sid: str = Form(..., alias="CallSid"),
    from_number: str = Form(..., alias="From"),
) -> Response:
    """
    Entry point for Twilio voice calls. Initializes conversation history
    and prompts the caller for their first request.
    """
    logger.info("Inbound voice call from %s (CallSid=%s)", from_number, call_sid)
    # Reset any prior session for this CallSid.
    _call_sessions.pop(call_sid, None)
    greeting = (
        "Namaste! You are connected to AmaSathi, the maternal health assistant. "
        "Please tell me how I can support you, then pause so I can respond."
    )
    twiml = _build_gather_twiml(greeting)
    return Response(content=twiml, media_type="application/xml")


@app.post("/twilio/voice/process", response_class=Response)
def process_twilio_voice(
    call_sid: str = Form(..., alias="CallSid"),
    speech_result: Optional[str] = Form(None, alias="SpeechResult"),
) -> Response:
    """
    Handle user utterances captured by Twilio's speech recognizer, produce a response,
    and continue the conversation until the caller indicates they are done.
    """
    history = _call_sessions.setdefault(call_sid, [])

    user_text = (speech_result or "").strip()
    if not user_text:
        logger.info("ASR returned empty transcript for CallSid=%s; reprompting.", call_sid)
        twiml = _build_gather_twiml("I didn't catch that clearly. Could you please repeat your question?")
        return Response(content=twiml, media_type="application/xml")

    logger.info("Caller said (CallSid=%s): %s", call_sid, user_text)
    history.append({"role": "user", "content": user_text})

    try:
        reply_text = chat_client.generate_reply(
            message=None,
            history=history,
            system_prompt=ASSISTANT_SYSTEM_PROMPT,
        )
    except (NvidiaConfigError, NvidiaGenerationError, ValueError) as exc:
        logger.exception("Failed to generate NVIDIA VIM reply for CallSid=%s: %s", call_sid, exc)
        twiml = _build_reply_twiml(
            "I'm having trouble responding right now. Let's try again later.",
            end_call=True,
        )
        _call_sessions.pop(call_sid, None)
        return Response(content=twiml, media_type="application/xml")

    reply_text = _condense_reply(reply_text)
    if not reply_text:
        reply_text = "I'm sorry, I couldn't form a response. Please repeat your question slowly."
    history.append({"role": "assistant", "content": reply_text})
    logger.info("Assistant reply for CallSid=%s: %s", call_sid, reply_text)

    if _should_end_conversation(user_text):
        twiml = _build_reply_twiml(reply_text, end_call=True)
        _call_sessions.pop(call_sid, None)
        return Response(content=twiml, media_type="application/xml")

    follow_up_twiml = _build_reply_twiml(reply_text)
    return Response(content=follow_up_twiml, media_type="application/xml")
