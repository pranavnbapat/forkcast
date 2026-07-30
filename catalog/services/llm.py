from __future__ import annotations

import json
import os
from base64 import b64encode
from collections.abc import Generator

import requests


class LLMServiceError(Exception):
    pass


class VLLMClient:
    def __init__(self):
        host = os.getenv("RUNPOD_VLLM_HOST", "").rstrip("/")
        if not host:
            raise LLMServiceError("RUNPOD_VLLM_HOST is not configured.")
        self.base_url = host if host.endswith("/v1") else f"{host}/v1"
        self.model = os.getenv("VLLM_MODEL", "").strip()
        if not self.model:
            raise LLMServiceError("VLLM_MODEL is not configured.")
        self.api_key = os.getenv("VLLM_API_KEY", "").strip()
        self.timeout_seconds = int(os.getenv("VLLM_TIMEOUT", "120"))

    def chat_json(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> tuple[str, dict]:
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise LLMServiceError(f"vLLM request failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMServiceError("vLLM response was missing chat content.") from exc
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMServiceError(f"vLLM returned invalid JSON: {content[:500]}") from exc
        return content, parsed

    def chat_json_stream(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
    ) -> Generator[tuple[str, str | dict], None, None]:
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "temperature": temperature,
            "stream": True,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.timeout_seconds,
            stream=True,
        )
        if response.status_code >= 400:
            raise LLMServiceError(f"vLLM request failed: HTTP {response.status_code} {response.text[:500]}")

        collected = []
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            payload_text = line[5:].strip()
            if payload_text == "[DONE]":
                break
            try:
                data = json.loads(payload_text)
            except json.JSONDecodeError:
                continue
            delta = (((data.get("choices") or [{}])[0]).get("delta") or {}).get("content", "")
            if delta:
                collected.append(delta)
                yield ("chunk", delta)

        full_text = "".join(collected)
        try:
            parsed = json.loads(full_text)
        except json.JSONDecodeError as exc:
            raise LLMServiceError(f"vLLM returned invalid streamed JSON: {full_text[:500]}") from exc
        yield ("complete", {"text": full_text, "json": parsed})


class VisionVLLMClient:
    def __init__(self):
        host = os.getenv("VISION_VLLM_HOST", os.getenv("RUNPOD_VLLM_HOST", "")).rstrip("/")
        if not host:
            raise LLMServiceError("VISION_VLLM_HOST is not configured.")
        self.base_url = host if host.endswith("/v1") else f"{host}/v1"
        self.model = os.getenv("VISION_MODEL", os.getenv("VLLM_MODEL", "")).strip()
        if not self.model:
            raise LLMServiceError("VISION_MODEL is not configured.")
        self.api_key = os.getenv("VISION_API_KEY", os.getenv("VLLM_API_KEY", "")).strip()
        self.timeout_seconds = int(os.getenv("VISION_TIMEOUT", os.getenv("VLLM_TIMEOUT", "120")))

    def chat_json_with_image(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_bytes: bytes,
        image_content_type: str,
        temperature: float = 0.2,
    ) -> tuple[str, dict]:
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        image_base64 = b64encode(image_bytes).decode("ascii")
        payload = {
            "model": self.model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{image_content_type};base64,{image_base64}"},
                        },
                    ],
                },
            ],
        }
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise LLMServiceError(f"Vision vLLM request failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMServiceError("Vision vLLM response was missing chat content.") from exc
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMServiceError(f"Vision vLLM returned invalid JSON: {content[:500]}") from exc
        return content, parsed
