"""
Local cluster, Qdrant and frontier provider client for StoneSage.
Bridges the engines in config.json cluster.* (coordinator, worker, embedder, vision), Qdrant memory,
and external frontier APIs (OpenRouter, OpenAI, Anthropic, Gemini). Model names, context sizes and
hardware come from system_profile at runtime.
"""

import os
import re
import json
import urllib.request
import urllib.error
import time
from lodestone import endpoints
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional, Generator, Tuple

try:
    from stonesage.reasoning_watchdog import GLOBAL_WATCHDOG, ReasoningLoopDetector
except ImportError:
    try:
        from .reasoning_watchdog import GLOBAL_WATCHDOG, ReasoningLoopDetector
    except ImportError:
        GLOBAL_WATCHDOG = None
        ReasoningLoopDetector = None

try:
    from stonesage.tool_harness import tool_registry, extract_tool_calls_from_text
except ImportError:
    try:
        from stonesage.tool_harness import tool_registry, extract_tool_calls_from_text
    except ImportError:
        tool_registry = None
        def extract_tool_calls_from_text(t): return []

class ClusterClient:
    def __init__(self, config: Dict[str, Any]):
        self.cluster_cfg = config.get("cluster", {})
        self.external_cfg = config.get("external_providers", {})
        self.coordinator_url = endpoints.url("coordinator", config)
        self.worker_url = endpoints.url("worker", config)
        self.embedder_url = endpoints.url("embedder", config)
        self.mcp_url = endpoints.url("mcp", config)
        self.qdrant_url = endpoints.url("qdrant", config)
        self.vision_url = endpoints.url("vision", config)
        self.config = config

    def _context_budget(self, model_name: str, reply_tokens: int) -> int:
        """Prompt budget = the engine's real context per slot minus the reply, from the live profile."""
        if model_name not in ("coordinator", "worker", "vision"):
            return 30000  # frontier APIs
        try:
            from lodestone import system_profile
            ctx = ((system_profile.get_profile(self.config).get("engines") or {}).get(model_name) or {}).get("ctx_per_slot")
        except Exception:
            ctx = None
        if not ctx:
            return 3000  # unknown: stay small rather than overflow
        return max(1024, int(ctx * 0.9) - min(reply_tokens, ctx // 2))

    def ping_endpoint(self, url: str, path: str = "/models", timeout: float = 2.0) -> Dict[str, Any]:
        """Probe latency and status of an AI endpoint, returning real model info if available."""
        start = time.perf_counter()
        target = f"{url.rstrip('/')}{path}"
        req = urllib.request.Request(target, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                latency = round((time.perf_counter() - start) * 1000, 1)
                model_name = None
                model_path = None
                try:
                    data = json.loads(resp.read().decode("utf-8"))
                    if "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
                        m0 = data["data"][0]
                        model_id = m0.get("id", "")
                        model_name = os.path.basename(model_id).replace(".gguf", "") if model_id else None
                        model_path = model_id
                    elif "models" in data and isinstance(data["models"], list) and len(data["models"]) > 0:
                        m0 = data["models"][0]
                        model_id = m0.get("key") or m0.get("name") or ""
                        model_name = os.path.basename(model_id).replace(".gguf", "") if model_id else None
                        model_path = model_id
                except Exception:
                    pass
                return {
                    "online": resp.status == 200,
                    "latency_ms": latency,
                    "model_name": model_name,
                    "model_path": model_path
                }
        except Exception as e:
            return {"online": False, "error": str(e)}

    def check_all_nodes(self) -> Dict[str, Any]:
        """Concurrently check status and latency across all local cluster nodes."""
        nodes = {
            "coordinator": (self.coordinator_url, "/models"),
            "worker": (self.worker_url, "/models"),
            "embedder_bge": (self.embedder_url, "/models"),
            "mcp_bridge": (self.mcp_url, "/health"),
            "qdrant_brain": (self.qdrant_url, "/readyz")
        }

        results = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_map = {name: executor.submit(self.ping_endpoint, url, path) for name, (url, path) in nodes.items()}
            for name, future in future_map.items():
                results[name] = future.result()

        return results

    def get_embedding(self, text: str) -> List[float]:
        """Generate 1024-dim dense vector embedding on the Embedder (:8003)."""
        # Bound text to < 800 characters to strictly respect BGE 512-token context limit
        safe_text = text[:800] if text else ""
        url = f"{self.embedder_url}/embeddings"
        payload = json.dumps({"input": safe_text, "model": "embedder"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["data"][0]["embedding"]

    def tokenize(self, text: str) -> List[str]:
        import re
        return re.findall(r"\b[a-zA-Z0-9_\.:-]+\b", text.lower())

    def compute_sparse_vector(self, text: str, k1: float = 1.2, b: float = 0.75, avg_len: float = 100.0) -> Dict[str, Any]:
        from collections import Counter
        import hashlib
        tokens = self.tokenize(text)
        if not tokens:
            return {"indices": [], "values": []}
        counts = Counter(tokens)
        doc_len = len(tokens)
        indices = []
        values = []
        for word, tf in sorted(counts.items()):
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF
            tf_weight = (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * (doc_len / avg_len)))
            indices.append(h)
            values.append(round(float(tf_weight), 4))
        return {"indices": indices, "values": values}

    def search_hybrid(self, query: str, collection_name: str = "obsidian_vault", limit: int = 5, filter_dict: Optional[Dict[str, Any]] = None, score_threshold: float = 0.0) -> List[Dict[str, Any]]:
        """Hybrid search combining dense BGE embeddings and sparse BM25 vectors via Qdrant Universal Query API and RRF."""
        dense_vec = self.get_embedding(query)
        sparse_vec = self.compute_sparse_vector(query)

        prefetch = [
            {
                "query": dense_vec,
                "using": "dense",
                "limit": limit * 3
            }
        ]
        if sparse_vec["indices"]:
            prefetch.append({
                "query": {"indices": sparse_vec["indices"], "values": sparse_vec["values"]},
                "using": "sparse",
                "limit": limit * 3
            })

        body: Dict[str, Any] = {
            "prefetch": prefetch,
            "query": {"rrf": {}},
            "limit": limit,
            "with_payload": True,
            "params": {"hnsw_ef": 128}
        }
        if filter_dict:
            must_clauses = [{"key": k, "match": {"value": v}} for k, v in filter_dict.items()]
            body["filter"] = {"must": must_clauses}

        query_url = f"{self.qdrant_url}/collections/{collection_name}/points/query"
        points = None
        try:
            req = urllib.request.Request(query_url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                points = data.get("result", {}).get("points")
        except Exception:
            pass

        if points is None:
            points = self.search_memory(query, collection_name=collection_name, limit=limit, filter_dict=filter_dict, score_threshold=score_threshold)

        if score_threshold > 0 and points:
            points = [p for p in points if float(p.get("score", 0.0)) >= score_threshold]
        return points or []

    def search_memory(self, query: str, collection_name: str = "codebase_knowledge", limit: int = 5, filter_dict: Optional[Dict[str, Any]] = None, score_threshold: float = 0.0) -> List[Dict[str, Any]]:
        """Semantic search against Qdrant using hardware embeddings and Universal Query API (Qdrant 1.19+)."""
        if collection_name == "obsidian_vault" or collection_name == "obsidian_brain":
            return self.search_hybrid(query, collection_name=collection_name, limit=limit, filter_dict=filter_dict, score_threshold=score_threshold)

        vector = self.get_embedding(query)
        query_url = f"{self.qdrant_url}/collections/{collection_name}/points/query"
        body: Dict[str, Any] = {
            "query": vector,
            "limit": limit,
            "with_payload": True,
            "params": {"hnsw_ef": 128}
        }
        if filter_dict:
            must_clauses = [{"key": k, "match": {"value": v}} for k, v in filter_dict.items()]
            body["filter"] = {"must": must_clauses}

        points = None
        try:
            req = urllib.request.Request(query_url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                points = data.get("result", {}).get("points")
        except Exception:
            pass

        if points is None:
            url = f"{self.qdrant_url}/collections/{collection_name}/points/search"
            payload = json.dumps({"vector": vector, "limit": limit, "with_payload": True}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                points = data.get("result", [])

        if score_threshold > 0 and points:
            points = [p for p in points if float(p.get("score", 0.0)) >= score_threshold]
        return points or []

    def store_memory(self, content: str, collection_name: str = "agent_memories", metadata: Optional[Dict[str, Any]] = None, point_id: Optional[str] = None) -> str:
        """Store knowledge or architectural decision into Qdrant.

        Pass a deterministic point_id (e.g. uuid5 of a stable key) to upsert instead of
        appending a new duplicate point on every call.
        """
        vector = self.get_embedding(content)
        point_id = point_id or str(uuid.uuid4())
        point = {
            "id": point_id,
            "vector": vector,
            "payload": {
                "content": content,
                "metadata": metadata or {},
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }
        }
        url = f"{self.qdrant_url}/collections/{collection_name}/points"
        payload = json.dumps({"points": [point]}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="PUT")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return point_id

    def prune_context_history(self, messages: List[Dict[str, Any]], budget_tokens: int = 3100) -> List[Dict[str, Any]]:
        """
        Sliding-window context pruner to prevent 'request exceeds available context size' HTTP 400 errors.
        Always preserves system prompt (index 0). Retains the most recent conversation turns from the tail.
        """
        if not messages or len(messages) <= 1:
            return messages

        sys_msg = None
        chat_turns = []
        for m in messages:
            if m.get("role") == "system" and sys_msg is None:
                sys_msg = m
            else:
                chat_turns.append(m)

        def est_tokens(msg: Dict[str, Any]) -> int:
            c = msg.get("content", "")
            if isinstance(c, list):
                txt_len = sum(len(p.get("text", "")) for p in c if isinstance(p, dict) and p.get("type") == "text")
                return int(txt_len / 3.6) + 30
            return int(len(str(c)) / 3.6) + 4

        sys_tokens = est_tokens(sys_msg) if sys_msg else 0
        rem_budget = max(budget_tokens - sys_tokens, 600)

        retained = []
        cur_tokens = 0
        for m in reversed(chat_turns):
            t = est_tokens(m)
            if cur_tokens + t > rem_budget and retained:
                break
            retained.insert(0, m)
            cur_tokens += t

        result = []
        if sys_msg:
            result.append(sys_msg)
        result.extend(retained)
        return result

    def stream_chat(self, target: str, messages: List[Dict[str, str]], params: Dict[str, Any]) -> Generator[str, None, None]:
        """
        Stream chat completions line-by-line via SSE.
        Supports the local engines (coordinator, worker, vision), Google Gemini (OpenAI-compatible),
        OpenAI, Anthropic, and OpenRouter with graceful local fallback if keys are unconfigured.
        """
        target_lower = target.lower()
        notice = None

        if target_lower.startswith("boost"):
            yield from self._stream_boost(target, messages, params)
            return

        if target_lower in ["worker", "fast", "draft"]:
            # Auto-failover check: probe worker endpoint connectivity
            w_stat = self.ping_endpoint(self.worker_url, timeout=0.8)
            if not w_stat.get("online"):
                c_stat = self.ping_endpoint(self.coordinator_url, timeout=0.8)
                if c_stat.get("online"):
                    base_url = self.coordinator_url
                    model_name = "coordinator"
                    notice = "\n> [!NOTE]\n> **Dynamic Failover**: Worker compute endpoint was unavailable. Automatically routed request to active Coordinator (:8001).\n\n"
                else:
                    base_url = self.worker_url
                    model_name = "worker"
            else:
                base_url = self.worker_url
                model_name = "worker"
            headers = {"Content-Type": "application/json"}
        elif target_lower in ["coordinator", "moe", "hermes", "hermes_agentic", "agentic"]:
            # "moe"/"hermes" are older config names for the coordinator engine
            base_url = self.coordinator_url
            model_name = "coordinator"
            headers = {"Content-Type": "application/json"}
        elif target_lower in ["vision", "vlm", "camera"]:
            base_url = self.vision_url
            model_name = "vision"
            headers = {"Content-Type": "application/json"}
        elif target_lower in ["antigravity", "antigravity_director", "hybrid", "hybrid_frontier", "director"]:
            gem_cfg = self.external_cfg.get("gemini", {})
            api_key = gem_cfg.get("api_key", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
            if api_key:
                base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
                model_name = "gemini-2.5-flash"
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
                notice = f"\n> [!TIP]\n> **🌌 Antigravity Frontier Director (Tier-1 Cloud Hybrid)**: High-level reasoning active. Heavy code & validation delegated to local cluster to optimize tokens.\n\n"
            else:
                notice = f"\n> [!TIP]\n> **🌌 Antigravity Frontier Director (Local Dual-GPU Stack)**: Running locally on the engine host (:8001 Coordinator + :8002 Worker) with Tier-1 Meta-Verifier invariants (0 cloud tokens consumed).\n\n"
                base_url = self.coordinator_url
                model_name = "coordinator"
                headers = {"Content-Type": "application/json"}
        elif "gemini" in target_lower:
            gem_cfg = self.external_cfg.get("gemini", {})
            api_key = gem_cfg.get("api_key", "").strip()
            if api_key:
                base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
                model_name = target
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            else:
                notice = f"\n> [!NOTE]\n> **Model Routing ({target})**: Google Gemini API key not configured in Settings (⚙️). Routing request automatically to your local **Primary Coordinator (:8001)** with zero token limits!\n\n"
                base_url = self.coordinator_url
                model_name = "coordinator"
                headers = {"Content-Type": "application/json"}
        elif "claude" in target_lower or "anthropic" in target_lower:
            ant_cfg = self.external_cfg.get("anthropic", {})
            api_key = ant_cfg.get("api_key", "").strip()
            if api_key:
                base_url = ant_cfg.get("base_url", "https://api.anthropic.com/v1")
                model_name = target
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            else:
                notice = f"\n> [!NOTE]\n> **Model Routing ({target})**: Anthropic API key not configured in Settings (⚙️). Routing request automatically to your local **Primary Coordinator (:8001)**.\n\n"
                base_url = self.coordinator_url
                model_name = "coordinator"
                headers = {"Content-Type": "application/json"}
        elif "gpt" in target_lower or "openai" in target_lower or "o3" in target_lower:
            oai_cfg = self.external_cfg.get("openai", {})
            api_key = oai_cfg.get("api_key", "").strip()
            if api_key:
                base_url = oai_cfg.get("base_url", "https://api.openai.com/v1")
                model_name = target
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            else:
                notice = f"\n> [!NOTE]\n> **Model Routing ({target})**: OpenAI API key not configured in Settings (⚙️). Routing request automatically to your local **Primary Coordinator (:8001)**.\n\n"
                base_url = self.coordinator_url
                model_name = "coordinator"
                headers = {"Content-Type": "application/json"}
        elif "deepseek" in target_lower:
            or_cfg = self.external_cfg.get("openrouter", {})
            api_key = or_cfg.get("api_key", "").strip()
            if api_key:
                base_url = or_cfg.get("base_url", "https://openrouter.ai/api/v1")
                model_name = "deepseek/deepseek-r1"
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            else:
                notice = f"\n> [!NOTE]\n> **Model Routing ({target})**: OpenRouter API key not configured in Settings (⚙️). Routing request automatically to your local **Primary Coordinator (:8001)**.\n\n"
                base_url = self.coordinator_url
                model_name = "coordinator"
                headers = {"Content-Type": "application/json"}
        else:
            base_url = self.coordinator_url
            model_name = "coordinator"
            headers = {"Content-Type": "application/json"}

        # Tiered memory & hardware grounding badges
        memory_badges = list(params.get("memory_badges", []))

        # Sanitize conversational history: remove error strings and leaked memory headers
        cleaned_messages = []
        for m in messages:
            m_copy = dict(m)
            c = m_copy.get("content")
            if isinstance(c, str):
                # Strip old Tiered Memory headers
                c = re.sub(r">\s*🧠\s*\*\*Tiered Memory Active\*\*:[^\n]*\n*", "", c)
                # Strip stream error tags
                c = re.sub(r"\[ERROR:\s*[^\]]+\]", "", c)
                c = re.sub(r">\s*\[!CAUTION\]\s*\n>\s*\*\*\[BACKEND ERROR\]\*\*:[^\n]*\n*", "", c)
                c = re.sub(r"\[STREAM INTERRUPTED BY OPERATOR\]", "", c)
                m_copy["content"] = c.strip()
            cleaned_messages.append(m_copy)
        dispatch_messages = cleaned_messages

        # RAG Context Augmentation via hybrid search with strict thresholding
        rag_enabled = params.get("rag", True)
        if rag_enabled and dispatch_messages:
            last_user_msg = next((m["content"] for m in reversed(dispatch_messages) if m.get("role") == "user"), "")
            if last_user_msg and len(last_user_msg.strip()) > 4:
                try:
                    q_lower = last_user_msg.lower()
                    # 1. Search Obsidian Brain (>= 0.72 score threshold)
                    obsidian_matches = self.search_hybrid(
                        last_user_msg.strip()[:300],
                        collection_name="obsidian_brain",
                        limit=2,
                        score_threshold=0.72
                    )
                    # 2. Search Codebase Knowledge (>= 0.72 score threshold)
                    codebase_matches = self.search_memory(
                        last_user_msg.strip()[:300],
                        collection_name="codebase_knowledge",
                        limit=2,
                        score_threshold=0.72
                    )
                    all_matches = (obsidian_matches or []) + (codebase_matches or [])

                    if all_matches:
                        rag_snippets = []
                        for pt in all_matches:
                            payload = pt.get("payload", {})
                            title = payload.get("note_title") or payload.get("title") or payload.get("path") or payload.get("name") or "Knowledge Reference"
                            content = payload.get("content") or payload.get("text") or ""
                            # Filter out skill docs unless user specifically asked for skills or tools
                            p_str = str(title).lower() + " " + str(payload.get("rel_path", "")).lower()
                            if ("skill" in p_str or "skills/" in p_str) and not any(k in q_lower for k in ["skill", "tool", "mcp", "manual", "guide"]):
                                continue
                            if "chat-" in p_str and not any(k in q_lower for k in ["chat", "session", "conversation", "prior"]):
                                continue

                            if content and content.strip():
                                rag_snippets.append(f"[{title}]:\n{content.strip()[:450]}")
                                clean_t = os.path.basename(str(title)).replace(".md", "")
                                b_name = f"Qdrant: {clean_t}"
                                if b_name not in memory_badges:
                                    memory_badges.append(b_name)
                        if rag_snippets:
                            rag_context = "\n\n".join(rag_snippets)
                            rag_injection = (
                                f"\n\n[RELEVANT KNOWLEDGE VAULT CONTEXT]:\n{rag_context}\n"
                                f"[END KNOWLEDGE VAULT CONTEXT]\n"
                                f"Reference this verified homelab/knowledge context when answering. "
                                f"Strictly limit claims to verified entities present in context and avoid fabricating unlisted hardware."
                            )
                            aug_messages = []
                            found_sys = False
                            for m in dispatch_messages:
                                if m.get("role") == "system" and not found_sys:
                                    aug_messages.append({"role": "system", "content": m["content"] + rag_injection})
                                    found_sys = True
                                else:
                                    aug_messages.append(dict(m))
                            if not found_sys:
                                aug_messages.insert(0, {"role": "system", "content": f"You are StoneSage AI, a homelab and coding copilot.{rag_injection}"})
                            dispatch_messages = aug_messages
                except Exception:
                    pass

        # If fallback notice exists, yield it first
        if notice:
            notice_chunk = {"choices": [{"delta": {"content": notice}}]}
            yield f"data: {json.dumps(notice_chunk)}\n\n"

        # If Tiered Memory or Hardware Grounding active, yield badge notice
        if memory_badges:
            badge_str = " • ".join(f"`{b}`" for b in memory_badges)
            mem_notice = f"> 🧠 **Tiered Memory Active**: Recalled {badge_str}\n\n"
            mem_chunk = {"choices": [{"delta": {"content": mem_notice}}]}
            yield f"data: {json.dumps(mem_chunk)}\n\n"

        # Inject Universal Tool Prompt if tool_registry available across all agents and models
        if tool_registry:
            tool_sp = tool_registry.compile_tool_system_prompt()
            found_s = False
            for m in dispatch_messages:
                if m.get("role") == "system":
                    m["content"] = m["content"] + "\n\n" + tool_sp
                    found_s = True
                    break
            if not found_s:
                dispatch_messages.insert(0, {"role": "system", "content": tool_sp})

        # Format multimodal image payloads if present
        multimodal_messages = []
        for m in dispatch_messages:
            imgs = m.get("images") or []
            if imgs and isinstance(m.get("content"), str):
                parts = [{"type": "text", "text": m["content"]}]
                for img_data in imgs:
                    parts.append({"type": "image_url", "image_url": {"url": img_data}})
                multimodal_messages.append({"role": m["role"], "content": parts})
            else:
                multimodal_messages.append(m)
        dispatch_messages = multimodal_messages

        # Context budgeting & sliding-window pruning
        # Prevents "request exceeds available context size" HTTP 400 errors.
        context_budget = self._context_budget(model_name, int(params.get("max_tokens", 768)))
        dispatch_messages = self.prune_context_history(dispatch_messages, budget_tokens=context_budget)

        # Optimal quantized model sampling invariant
        temp_default = 0.70 if model_name in ["coordinator", "worker", "moe"] else 0.4
        url = f"{base_url.rstrip('/')}/chat/completions"
        max_tok = min(int(params.get("max_tokens", 768)), 16384)
        body = {
            "model": model_name,
            "messages": dispatch_messages,
            "stream": True,
            "temperature": float(params.get("temperature", temp_default)),
            "max_tokens": max_tok
        }
        if tool_registry and "tools" not in body:
            body["tools"] = tool_registry.get_openai_tool_definitions()

        if model_name in ["coordinator", "worker", "moe"]:
            body["min_p"] = float(params.get("min_p", 0.06))
            body["presence_penalty"] = float(params.get("presence_penalty", 0.25))
            body["frequency_penalty"] = float(params.get("frequency_penalty", 0.20))
            body["repeat_penalty"] = float(params.get("repeat_penalty", 1.15))
            body["top_p"] = float(params.get("top_p", 0.95))
            enable_think = params.get("enable_thinking")
            if enable_think is not None:
                body["chat_template_kwargs"] = {"enable_thinking": bool(enable_think)}
            elif not params.get("deep_reasoning", False):
                body["chat_template_kwargs"] = {"enable_thinking": False}

        for key in ["top_k", "repetition_penalty"]:
            if key in params and params[key] is not None:
                body[key] = params[key]

        # Signal preemption to pause background exploration loop immediately
        try:
            self.signal_preemption(reason=f"chat_{model_name}", in_flight=True)
        except Exception:
            pass

        has_done = False
        turn_count = 0
        max_tool_turns = int(params.get("max_tool_turns", 15))

        try:
            while turn_count < max_tool_turns:
                turn_count += 1
                dispatch_messages = self.prune_context_history(dispatch_messages, budget_tokens=context_budget)
                body["messages"] = dispatch_messages
                req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
                accumulated_turn_content = []
                accumulated_tool_calls = []
                loop_detector = ReasoningLoopDetector(min_repeats=3) if ReasoningLoopDetector else None

                try:
                    with urllib.request.urlopen(req, timeout=45) as resp:
                        for line in resp:
                            decoded = line.decode("utf-8", errors="replace")
                            if not decoded.strip():
                                continue
                            if "[DONE]" in decoded:
                                break

                            if decoded.startswith("data: "):
                                try:
                                    chunk_json = json.loads(decoded[6:].strip())
                                    delta = chunk_json.get("choices", [{}])[0].get("delta", {})

                                    if "tool_calls" in delta and delta["tool_calls"]:
                                        for tc in delta["tool_calls"]:
                                            fn = tc.get("function", {})
                                            if fn.get("name"):
                                                accumulated_tool_calls.append({"name": fn.get("name"), "arguments": fn.get("arguments", "")})

                                    content_delta = delta.get("content", "")
                                    if content_delta:
                                        accumulated_turn_content.append(content_delta)
                                        if loop_detector:
                                            is_loop, phrase = loop_detector.ingest_chunk(content_delta)
                                            if is_loop:
                                                if GLOBAL_WATCHDOG:
                                                    GLOBAL_WATCHDOG.record_intercept(phrase, model=model_name)
                                                alert_payload = {
                                                    "choices": [{"delta": {"content": f"\n\n> [!WARNING]\n> **[WATCHDOG INTERCEPT]**: Repetition loop detected on '{phrase}'. Aborted GPU stream & dispatched agent nudge.\n\n"}}],
                                                    "watchdog_intercept": True,
                                                    "intercept_phrase": phrase
                                                }
                                                yield f"data: {json.dumps(alert_payload)}\n\n"
                                                break
                                except Exception:
                                    pass

                            yield decoded

                except Exception as e:
                    detail = str(e)
                    if hasattr(e, "read"):
                        try:
                            raw_body = e.read().decode("utf-8", errors="replace")
                            err_json = json.loads(raw_body)
                            if "error" in err_json and isinstance(err_json["error"], dict) and "message" in err_json["error"]:
                                detail = f"{e}: {err_json['error']['message']}"
                            elif "error" in err_json:
                                detail = f"{e}: {err_json['error']}"
                        except Exception:
                            pass
                    err_msg = f"\n\n> [!CAUTION]\n> **[BACKEND STREAM ERROR]**: {detail}\n\n"
                    err_delta = {"choices": [{"delta": {"content": err_msg}}]}
                    yield f"data: {json.dumps(err_delta)}\n\n"
                    err_obj = {"error": {"message": detail, "type": "backend_error"}}
                    yield f"data: {json.dumps(err_obj)}\n\n"
                    break

                turn_text = "".join(accumulated_turn_content)
                extracted = extract_tool_calls_from_text(turn_text) if extract_tool_calls_from_text else []

                all_detected_calls = []
                for tc in accumulated_tool_calls:
                    fn_name = tc.get("name")
                    raw_args = tc.get("arguments")
                    try:
                        args_obj = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    except Exception:
                        args_obj = {}
                    if fn_name:
                        all_detected_calls.append((fn_name, args_obj))
                for fn_name, args_obj in extracted:
                    if not any(c[0] == fn_name for c in all_detected_calls):
                        all_detected_calls.append((fn_name, args_obj))

                if not all_detected_calls:
                    has_done = True
                    break

                # Execute detected tool calls and stream results back
                for fn_name, fn_args in all_detected_calls:
                    call_id = f"call_{uuid.uuid4().hex[:8]}"
                    tool_call_evt = {
                        "type": "tool_call",
                        "name": fn_name,
                        "arguments": fn_args,
                        "call_id": call_id
                    }
                    yield f"data: {json.dumps(tool_call_evt)}\n\n"

                    tool_res = {}
                    if tool_registry:
                        tool_res = tool_registry.execute_tool(fn_name, fn_args, workspace_path=params.get("workspace_path", ""))
                    else:
                        tool_res = {"ok": False, "error": "Tool registry not loaded."}

                    tool_res_evt = {
                        "type": "tool_result",
                        "name": fn_name,
                        "result": tool_res,
                        "call_id": call_id
                    }
                    yield f"data: {json.dumps(tool_res_evt)}\n\n"

                    dispatch_messages.append({
                        "role": "assistant",
                        "content": turn_text or f"<|tool_call>call:{fn_name}{json.dumps(fn_args)}<tool_call|>"
                    })
                    dispatch_messages.append({
                        "role": "tool",
                        "name": fn_name,
                        "content": json.dumps(tool_res)
                    })

            has_done = True
        finally:
            try:
                self.signal_preemption(reason=f"chat_{model_name}_done", in_flight=False)
            except Exception:
                pass
            if not has_done:
                yield "data: [DONE]\n\n"

    def _stream_boost(self, target: str, messages: List[Dict[str, Any]], params: Dict[str, Any]) -> Generator[str, None, None]:
        yield from self._stream_boost_with(self.stream_chat, target, messages, params)

    def _stream_boost_with(self, local_stream, target: str, messages: List[Dict[str, Any]],
                           params: Dict[str, Any]) -> Generator[str, None, None]:
        """Free cloud sources via backend/boost. `target` = "boost", "boost:<provider>" or "boost:<provider>/<model>".

        No RAG and no tool registry here: Obsidian/Qdrant recall can carry home data (and, until it is cleaned,
        a token), and tool results would leave the house. Anything the router refuses runs on the coordinator."""
        def note(text: str) -> str:
            return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n"

        surface = params.get("boost_surface", "chat")
        pinned = target.split(":", 1)[1] if ":" in target else None
        try:
            from lodestone import boost
            router = boost.get_router()
        except Exception:
            router = None
        if router is None or not router.enabled(surface):
            yield note(f"> [!NOTE]\n> **Boost** is off for {surface} (⚡ Boost window). Answering on the local coordinator.\n\n")
            yield from local_stream("coordinator", messages, params)
            return
        if any(m.get("images") for m in messages):
            yield note("> [!NOTE]\n> **Boost**: pictures never leave the house. Answering on the local coordinator.\n\n")
            yield from local_stream("coordinator", messages, params)
            return
        keep = ("role", "content", "name", "tool_calls", "tool_call_id")
        msgs = [{k: v for k, v in m.items() if k in keep} for m in messages]
        res = router.open_stream(msgs, surface, declared="code" if surface == "workspaces" else "general",
                                 max_tokens=min(int(params.get("max_tokens", 1024)), 8192),
                                 temperature=float(params.get("temperature", 0.5)), pinned=pinned, allow_local=False)
        if not res.get("ok"):
            why = "; ".join((res.get("skipped") or []) + (res.get("tried") or []))[:600]
            yield note(f"> [!NOTE]\n> **Boost**: no free source could take this ({res.get('class')} content): {why}. "
                       "Answering on the local coordinator.\n\n")
            yield from local_stream("coordinator", messages, params)
            return
        yield note(f"> ⚡ **Boost**: {res['label']} · `{res['model']}`\n\n")
        done = False
        for line in boost.iter_sse(res["response"]):
            if line.strip() == "data: [DONE]":
                done = True
            yield line.rstrip("\n") + "\n\n"
        if not done:
            yield "data: [DONE]\n\n"

    def multi_model_arena_query(self, models: List[str], prompt: str) -> Dict[str, str]:
        """Execute concurrent generation across multiple models for side-by-side evaluation."""
        messages = [{"role": "user", "content": prompt}]
        
        def run_model(target: str) -> Tuple[str, str]:
            chunks = []
            for chunk in self.stream_chat(target, messages, {"max_tokens": 1024}):
                if chunk.startswith("data: ") and not "[DONE]" in chunk:
                    try:
                        delta = json.loads(chunk[6:])["choices"][0]["delta"].get("content") or ""
                        if delta:
                            chunks.append(delta)
                    except Exception:
                        pass
            return target, "".join(chunks)

        results = {}
        with ThreadPoolExecutor(max_workers=len(models)) as executor:
            future_to_model = {executor.submit(run_model, m): m for m in models}
            for future in future_to_model:
                try:
                    target, output = future.result()
                    results[target] = output
                except Exception as e:
                    results[future_to_model[future]] = f"Error: {str(e)}"

        return results

    def synthesize_answers(self, prompt: str, answers: Dict[str, str]) -> str:
        """Use the Primary Coordinator (:8001) to merge and synthesize multiple answers."""
        combined_text = "\n\n".join([f"### Model Response from [{k}]:\n{v}" for k, v in answers.items()])
        synth_prompt = (
            f"You are a master synthesis arbiter. Two or more AI models answered the following user prompt:\n\n"
            f"USER PROMPT: {prompt}\n\n"
            f"MODEL OUTPUTS:\n{combined_text}\n\n"
            f"Analyze all outputs. Identify the most accurate code, best architecture, and clearest insights. "
            f"Synthesize them into one definitive, optimal, production-grade response."
        )
        messages = [
            {"role": "system", "content": "You are a master code and architecture synthesis engine."},
            {"role": "user", "content": synth_prompt}
        ]
        
        url = f"{self.coordinator_url}/chat/completions"
        body = {"model": "coordinator", "messages": messages, "stream": False, "temperature": 0.2}
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"Synthesis error: {str(e)}"

    def signal_preemption(self, reason: str = "interactive_user", in_flight: bool = False) -> Dict[str, Any]:
        """Signal real-time interactive activity to the cluster to pause the 24/7 autonomous loop."""
        try:
            url = f"{self.mcp_url}/api/preemption/signal"
            payload = json.dumps({"reason": reason, "in_flight": in_flight}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": str(e), "is_preempted": False}

    def get_preemption_status(self) -> Dict[str, Any]:
        """Check whether the cluster is currently preempted for user activity."""
        try:
            url = f"{self.mcp_url}/api/preemption/status"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": str(e), "is_preempted": False}
