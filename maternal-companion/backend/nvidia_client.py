from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from openai import OpenAI


class NvidiaConfigError(RuntimeError):
    """Raised when the NVIDIA client is missing mandatory configuration."""


class NvidiaGenerationError(RuntimeError):
    """Raised when the NVIDIA model call fails."""


DEFAULT_API_URL = "https://integrate.api.nvidia.com/v1"


@dataclass
class NvidiaSettings:
    api_key: str
    model: str
    api_url: str = DEFAULT_API_URL
    temperature: float = 0.2
    top_p: float = 0.7
    max_tokens: Optional[int] = None
    system_prompt: Optional[str] = None

    @classmethod
    def from_env(cls) -> "NvidiaSettings":
        api_key = os.getenv("NVIDIA_API_KEY")
        model = os.getenv("NVIDIA_VIM_MODEL")
        if not api_key or not model:
            raise NvidiaConfigError("Set NVIDIA_API_KEY and NVIDIA_VIM_MODEL environment variables.")

        api_url = os.getenv("NVIDIA_API_URL", DEFAULT_API_URL)
        system_prompt = os.getenv("NVIDIA_SYSTEM_PROMPT")
        temperature_str = os.getenv("NVIDIA_TEMPERATURE")
        top_p_str = os.getenv("NVIDIA_TOP_P")
        max_tokens_str = os.getenv("NVIDIA_MAX_TOKENS")

        temperature = cls._safe_float(temperature_str, default=0.2)
        top_p = cls._safe_float(top_p_str, default=0.7)
        max_tokens = cls._safe_int(max_tokens_str)

        return cls(
            api_key=api_key,
            model=model,
            api_url=api_url,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )

    @staticmethod
    def _safe_float(value: Optional[str], default: float) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(value: Optional[str]) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class NvidiaVimClient:
    """Simple chat completion client for NVIDIA VIM models."""

    def __init__(self, settings: Optional[NvidiaSettings] = None) -> None:
        self.settings = settings or NvidiaSettings.from_env()
        self._client = OpenAI(api_key=self.settings.api_key, base_url=self.settings.api_url)

    def generate_reply(
        self,
        message: Optional[str],
        *,
        history: Optional[List[Dict[str, str]]] = None,
        system_prompt: Optional[str] = None,
        stream: bool = False,
    ) -> str:
        if message is None and not history:
            raise ValueError("Provide a user message or conversation history to generate a reply.")

        messages: List[Dict[str, str]] = []
        if history:
            for item in history:
                role = item.get("role")
                content = item.get("content")
                if role and content:
                    messages.append({"role": role, "content": content})

        prompt = system_prompt or self.settings.system_prompt
        if prompt:
            if any(msg.get("role") == "system" for msg in messages):
                for msg in messages:
                    if msg.get("role") == "system":
                        msg["content"] = prompt
                        break
            else:
                messages.insert(0, {"role": "system", "content": prompt})

        if message is not None:
            stripped = message.strip()
            if not stripped:
                raise ValueError("Message payload must be a non-empty string.")
            messages.append({"role": "user", "content": stripped})

        if not any(msg.get("role") == "user" for msg in messages):
            raise ValueError("Conversation history must include at least one user message.")

        request_args = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "top_p": self.settings.top_p,
            "stream": stream,
        }
        if self.settings.max_tokens is not None:
            request_args["max_tokens"] = self.settings.max_tokens

        try:
            if stream:
                completion_stream = self._client.chat.completions.create(**request_args)
                chunks = []
                for chunk in completion_stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        chunks.append(delta)
                return "".join(chunks)

            request_args["stream"] = False
            completion = self._client.chat.completions.create(**request_args)
        except Exception as exc:
            raise NvidiaGenerationError(f"Failed to generate reply: {exc}") from exc

        try:
            return completion.choices[0].message.content
        except (AttributeError, IndexError, KeyError, TypeError) as exc:
            raise NvidiaGenerationError("Unexpected NVIDIA API response format.") from exc


_cached_client: Optional[NvidiaVimClient] = None


def get_vim_client(refresh: bool = False) -> NvidiaVimClient:
    """Return a cached client instance so the FastAPI layer can reuse it."""
    global _cached_client
    if refresh or _cached_client is None:
        _cached_client = NvidiaVimClient()
    return _cached_client
