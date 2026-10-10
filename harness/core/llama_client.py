"""
Asynchronous llama.cpp & LM Studio Client.
Handles SSE token streaming, separates <think> reasoning blocks, calculates live metrics,
and supports instant stream cancellation for /nudge out-of-band interventions.
"""

import time
import json
import asyncio
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional, AsyncIterator, Callable
from dataclasses import dataclass, field

logger = logging.getLogger("Harness.LlamaClient")

@dataclass
class StreamChunk:
    chunk_type: str  # "thought", "output", "tool_call", "start", "done", "error", "nudged"
    content: str
    token_count: int = 0
    elapsed_ms: float = 0.0
    tokens_per_sec: float = 0.0
    model_name: str = ""

class LlamaClient:
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._cached_model: Optional[str] = None

    async def get_loaded_model(self) -> str:
        """Dynamically polls the server's /models endpoint to retrieve loaded model ID."""
        if self._cached_model:
            return self._cached_model
        models_url = f"{self.base_url}/models"
        try:
            loop = asyncio.get_running_loop()
            def _fetch():
                req = urllib.request.Request(models_url, headers={"User-Agent": "LlamaClient"})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            data = await loop.run_in_executor(None, _fetch)
            models = data.get("data", [])
            if models:
                self._cached_model = models[0].get("id", "Unknown")
                return self._cached_model
        except Exception as e:
            logger.warning(f"Could not poll models from {models_url}: {e}")
        return "local-model"

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        request_id: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        min_p: float = 0.06,
        presence_penalty: float = 0.25,
        frequency_penalty: float = 0.15,
        repeat_penalty: float = 1.15,
        max_tokens: int = 4096,
        stop_sequences: Optional[List[str]] = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Streams completions from llama-server / LM Studio OpenAI-compatible endpoint.
        Separates <think> tags into distinct 'thought' events from 'output' text.
        """
        model_name = model or await self.get_loaded_model()
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "min_p": min_p,
            "presence_penalty": presence_penalty,
            "frequency_penalty": frequency_penalty,
            "repeat_penalty": repeat_penalty,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if stop_sequences:
            payload["stop"] = stop_sequences

        url = f"{self.base_url}/chat/completions"
        start_time = time.time()
        token_count = 0
        in_think_block = False
        ttft_recorded = False

        yield StreamChunk(chunk_type="start", content="", model_name=model_name)

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Harness-Client"},
                method="POST"
            )

            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=self.timeout))

            # Read stream chunks line by line
            while True:
                line_bytes = await loop.run_in_executor(None, resp.readline)
                if not line_bytes:
                    break

                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue

                raw_data = line[5:].strip()
                if raw_data == "[DONE]":
                    break

                try:
                    chunk_json = json.loads(raw_data)
                    choices = chunk_json.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})
                    reasoning = delta.get("reasoning_content", "")
                    content = delta.get("content", "")
                    if not reasoning and not content:
                        continue

                    token_count += 1
                    now = time.time()
                    elapsed = max(now - start_time, 0.001)
                    tps = round(token_count / elapsed, 1)

                    if not ttft_recorded:
                        ttft_recorded = True

                    # Direct reasoning_content streaming (models that emit a separate thinking stream)
                    if reasoning:
                        yield StreamChunk(
                            chunk_type="thought",
                            content=reasoning,
                            token_count=token_count,
                            elapsed_ms=round(elapsed * 1000, 1),
                            tokens_per_sec=tps,
                            model_name=model_name,
                        )
                        continue

                    # Parse <think>...</think> boundaries in content
                    if "<think>" in content:
                        in_think_block = True
                        parts = content.split("<think>", 1)
                        if parts[0]:
                            yield StreamChunk(
                                chunk_type="output",
                                content=parts[0],
                                token_count=token_count,
                                elapsed_ms=round(elapsed * 1000, 1),
                                tokens_per_sec=tps,
                                model_name=model_name,
                            )
                        if parts[1]:
                            yield StreamChunk(
                                chunk_type="thought",
                                content=parts[1],
                                token_count=token_count,
                                elapsed_ms=round(elapsed * 1000, 1),
                                tokens_per_sec=tps,
                                model_name=model_name,
                            )
                        continue

                    if "</think>" in content:
                        parts = content.split("</think>", 1)
                        if parts[0]:
                            yield StreamChunk(
                                chunk_type="thought",
                                content=parts[0],
                                token_count=token_count,
                                elapsed_ms=round(elapsed * 1000, 1),
                                tokens_per_sec=tps,
                                model_name=model_name,
                            )
                        in_think_block = False
                        if parts[1]:
                            yield StreamChunk(
                                chunk_type="output",
                                content=parts[1],
                                token_count=token_count,
                                elapsed_ms=round(elapsed * 1000, 1),
                                tokens_per_sec=tps,
                                model_name=model_name,
                            )
                        continue

                    # Stream text chunk
                    c_type = "thought" if in_think_block else "output"
                    yield StreamChunk(
                        chunk_type=c_type,
                        content=content,
                        token_count=token_count,
                        elapsed_ms=round(elapsed * 1000, 1),
                        tokens_per_sec=tps,
                        model_name=model_name,
                    )

                except json.JSONDecodeError:
                    continue

            # Stream finished normally
            elapsed = max(time.time() - start_time, 0.001)
            tps = round(token_count / elapsed, 1)
            yield StreamChunk(
                chunk_type="done",
                content="",
                token_count=token_count,
                elapsed_ms=round(elapsed * 1000, 1),
                tokens_per_sec=tps,
                model_name=model_name,
            )

        except GeneratorExit:
            # Generator closed early (e.g. operator interrupt / aclose()); exit cleanly without yielding
            return

        except asyncio.CancelledError:
            logger.info(f"Stream {request_id} interrupted via out-of-band signal.")
            return

        except Exception as e:
            logger.error(f"Stream error for {request_id}: {e}")
            yield StreamChunk(chunk_type="error", content=str(e), model_name=model_name)

        finally:
            # Non-yielding cleanup only (PEP 525 strict invariant)
            pass

    def abort_request(self, request_id: str) -> bool:
        """Cancels an in-flight stream task immediately."""
        task = self._active_tasks.pop(request_id, None)
        if task and not task.done():
            task.cancel()
            logger.info(f"Cancelled stream task {request_id}.")
            return True
        return False
