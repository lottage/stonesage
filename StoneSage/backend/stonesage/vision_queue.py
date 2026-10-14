"""
Vision queue (2026-10-14): background vision work that cannot run while the vision engine is lent out is kept, not dropped.

Why: the garden may hold the RX 6600 (an engine-lease layout that evicts the vision server; docs/garden/DESIGN.md "Vision during garden
time"). Background callers of the vision model (the wildlife sentry on the engine host, the PTZ patrol, commentary reads, sighting boxes)
use Frigate's detections straight away where they can; the frames that really needed the vision model wait here and are read when the
engine is back, then handed to their source as LATE results.

What an item is: a frame (JPEG file) and a small JSON record in `data/vision_queue/` -- source (patrol | commentary | box | sentry),
camera, the time of the frame, the prompt to ask (the source builds it, so no prompt text lives in the platform), max_tokens, temperature
and a callback key the source uses to find its place again (a patrol frame, a card, a sentry snapshot). Nothing here knows what a sighting is.

Order and limits: oldest first. An item older than `max_age_h` (12) is dropped with a trace record (dropped_age); over the size caps the
oldest goes (dropped_cap: per camera, in total, in bytes). A frame the vision engine fails on is tried `max_tries` times and then dropped
(failed), so one bad frame cannot block the rest.

Processing: a loop thread (`start()`) and `kick()` (called right after a release) read the queue whenever the engine is available
(`available_fn()`), one frame at a time with `gap_s` between, and stop at once if the engine is lent out again. A source with an in-process
handler (register_handler) gets `handler(item, text, frame)`; a source without one (the sentry on another machine) pulls its results with
`results(source)` and acknowledges them with `ack(ids)`; either way the item is deleted when delivered.

Config (config.json `vision_queue`): enabled (true), max_age_h (12), max_items (300), max_per_camera (40), max_mb (150), poll_s (15),
gap_s (0.5), max_tries (3), key (the sentry's scoped key, see hearth/app.py).
Trace kind `vision_queue`: event (queued | processed | delivered_late | dropped_age | dropped_cap | failed), source, camera, item,
queue_age_s, ms. Prometheus: stonesage_vision_queue_depth{source}, stonesage_vision_queue_oldest_age_seconds,
stonesage_vision_queue_events_total{event,source}.

Layers: platform (layer 1). It imports nothing from an app; apps register their handlers.
"""

import base64
import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

DEFAULTS: Dict[str, Any] = {
    "enabled": True, "max_age_h": 12, "max_items": 300, "max_per_camera": 40, "max_mb": 150,
    "poll_s": 15, "gap_s": 0.5, "max_tries": 3,
}
EVENTS = ("queued", "processed", "delivered_late", "dropped_age", "dropped_cap", "failed")
MAX_FRAME_BYTES = 3 * 1024 * 1024


class VisionQueue:
    def __init__(self, cfg_fn: Callable[[], Dict[str, Any]], vision_fn: Callable[[bytes, str, int, float], str],
                 available_fn: Callable[[], bool], trace: Optional[Callable[[Dict[str, Any]], None]] = None,
                 data_dir: Optional[str] = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        """vision_fn(jpeg, prompt, max_tokens, temperature) -> the model's text (raises when the engine fails);
        available_fn() -> the vision engine is there (not lent out, answering)."""
        self.cfg_fn, self.vision_fn, self.available_fn, self.trace = cfg_fn, vision_fn, available_fn, trace
        self.dir, self.clock, self.sleep = data_dir, clock, sleep
        self.handlers: Dict[str, Callable[[Dict[str, Any], str, bytes], Any]] = {}
        self.counts: Dict[tuple, int] = {}
        self.items: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._busy = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._kicking = False
        self._seq = 0
        if self.dir:
            os.makedirs(self.dir, exist_ok=True)
            self._load()

    # ------------------------------------------------------------------ config / disk ----
    def cfg(self) -> Dict[str, Any]:
        raw = (self.cfg_fn() or {}).get("vision_queue")
        out = dict(DEFAULTS)
        if isinstance(raw, dict):
            out.update({k: v for k, v in raw.items() if k in DEFAULTS})
        for k, default in (("max_age_h", 12), ("max_items", 300), ("max_per_camera", 40), ("max_mb", 150), ("poll_s", 15),
                           ("gap_s", 0.5), ("max_tries", 3)):
            try:
                out[k] = max(0.0, float(out[k]))
            except (TypeError, ValueError):
                out[k] = float(default)
        out["poll_s"] = max(0.05, out["poll_s"])
        out["max_tries"] = max(1, int(out["max_tries"]))
        return out

    def enabled(self) -> bool:
        return bool(self.cfg()["enabled"])

    def _paths(self, item_id: str):
        return os.path.join(self.dir, item_id + ".json"), os.path.join(self.dir, item_id + ".jpg")

    def _load(self) -> None:
        for name in sorted(os.listdir(self.dir)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.dir, name), encoding="utf-8") as f:
                    item = json.load(f)
                if isinstance(item, dict) and item.get("id") and os.path.exists(self._paths(item["id"])[1]):
                    self.items[item["id"]] = item
            except (OSError, ValueError):
                continue

    def _write(self, item: Dict[str, Any]) -> None:
        if not self.dir:
            return
        tmp = self._paths(item["id"])[0] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(item, f)
        os.replace(tmp, self._paths(item["id"])[0])

    def _frame(self, item_id: str) -> bytes:
        if not self.dir:
            return self.items.get(item_id, {}).get("_frame", b"")
        with open(self._paths(item_id)[1], "rb") as f:
            return f.read()

    def _delete(self, item_id: str) -> None:
        self.items.pop(item_id, None)
        if self.dir:
            for p in self._paths(item_id):
                try:
                    os.remove(p)
                except OSError:
                    pass

    # ------------------------------------------------------------------ trace / counters ----
    def _event(self, event: str, item: Dict[str, Any], **fields: Any) -> None:
        key = (event, item.get("source") or "?")
        self.counts[key] = self.counts.get(key, 0) + 1
        if self.trace:
            rec = {"kind": "vision_queue", "at": round(self.clock(), 3), "event": event, "outcome": event,
                   "source": item.get("source"), "camera": item.get("camera"), "item": item.get("id"),
                   "frame_kind": item.get("kind"), "queue_age_s": round(max(0.0, self.clock() - float(item.get("at") or 0))),
                   "triggers": ["vision_queue_failed"] if event == "failed" else [], **fields}
            try:
                self.trace(rec)
            except Exception:
                pass

    # ------------------------------------------------------------------ putting things in ----
    def register_handler(self, source: str, fn: Callable[[Dict[str, Any], str, bytes], Any]) -> None:
        """An in-process source takes its late results through fn(item, text, frame); without one, results wait for results()/ack()."""
        self.handlers[source] = fn

    def has_key(self, source: str, key: str) -> bool:
        with self._lock:
            return any(i["source"] == source and i.get("key") == key and i["state"] == "queued" for i in self.items.values())

    def enqueue(self, source: str, camera: str, kind: str, frame: bytes, prompt: str, max_tokens: int = 80,
                temperature: float = 0.1, key: str = "", meta: Optional[Dict[str, Any]] = None, at: Optional[float] = None,
                dedupe: bool = False) -> Dict[str, Any]:
        if not self.enabled():
            return {"ok": False, "error": "the vision queue is switched off"}
        if not frame or len(frame) > MAX_FRAME_BYTES:
            return {"ok": False, "error": "no frame, or it is over %d bytes" % MAX_FRAME_BYTES}
        now = self.clock()
        with self._lock:
            if dedupe and key and self.has_key(source, key):
                return {"ok": True, "queued": False, "duplicate": True}
            self._seq += 1                                  # two frames in the same millisecond keep their order
            item_id = "%013d-%06d-%s" % (int(now * 1000), self._seq % 1000000, uuid.uuid4().hex[:4])
            item = {"id": item_id, "source": source, "camera": camera, "kind": kind, "at": min(float(at or now), now),
                    "queued_at": now, "prompt": prompt, "max_tokens": int(max_tokens), "temperature": float(temperature),
                    "key": key, "meta": meta or {}, "state": "queued", "tries": 0}
            if self.dir:
                with open(self._paths(item_id)[1], "wb") as f:
                    f.write(frame)
            else:
                item["_frame"] = frame
            self._write(item)
            self.items[item_id] = item
            self._event("queued", item)
            self._enforce_caps()
            depth = sum(1 for i in self.items.values() if i["state"] == "queued")
        return {"ok": True, "queued": item_id in self.items, "id": item_id, "depth": depth}

    def _enforce_caps(self) -> None:
        cfg = self.cfg()
        self._expire()
        live = sorted(self.items.values(), key=lambda i: i["id"])
        by_cam: Dict[str, List[Dict[str, Any]]] = {}
        for i in live:
            by_cam.setdefault("%s|%s" % (i["source"], i.get("camera")), []).append(i)
        for rows in by_cam.values():
            for i in rows[:max(0, len(rows) - int(cfg["max_per_camera"]))]:
                self._drop(i, "dropped_cap", why="per-camera cap")
        live = sorted(self.items.values(), key=lambda i: i["id"])
        for i in live[:max(0, len(live) - int(cfg["max_items"]))]:
            self._drop(i, "dropped_cap", why="item cap")
        if self.dir:
            limit = cfg["max_mb"] * 1024 * 1024
            total = 0
            sizes = {}
            for i in self.items.values():
                try:
                    sizes[i["id"]] = os.path.getsize(self._paths(i["id"])[1])
                except OSError:
                    sizes[i["id"]] = 0
                total += sizes[i["id"]]
            for i in sorted(self.items.values(), key=lambda i: i["id"]):
                if total <= limit:
                    break
                total -= sizes.get(i["id"], 0)
                self._drop(i, "dropped_cap", why="size cap")

    def _drop(self, item: Dict[str, Any], event: str, **fields: Any) -> None:
        self._event(event, item, **fields)
        self._delete(item["id"])

    def _expire(self) -> int:
        limit = self.cfg()["max_age_h"] * 3600
        n = 0
        for item in list(self.items.values()):
            if self.clock() - float(item.get("delivered_at") or item["at"]) > limit:
                self._drop(item, "dropped_age", state=item["state"])
                n += 1
        return n

    def expire(self) -> int:
        with self._lock:
            return self._expire()

    # ------------------------------------------------------------------ taking things out ----
    def process(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """Read the queued frames, oldest first, while the engine is available. Safe to call from several places: one run at a time."""
        if not self._busy.acquire(blocking=False):
            return {"ok": True, "busy": True, "processed": 0}
        done = failed = 0
        try:
            while limit is None or done + failed < limit:
                with self._lock:
                    self._expire()
                    queued = sorted((i for i in self.items.values() if i["state"] == "queued"), key=lambda i: i["id"])
                if not queued:
                    break
                if not self.available_fn():
                    break
                item = queued[0]
                t0 = self.clock()
                try:
                    frame = self._frame(item["id"])
                    text = self.vision_fn(frame, item["prompt"], item["max_tokens"], item["temperature"])
                except Exception as e:
                    with self._lock:
                        item["tries"] = int(item.get("tries", 0)) + 1
                        if item["tries"] >= self.cfg()["max_tries"]:
                            self._drop(item, "failed", error="%s: %s" % (type(e).__name__, str(e)[:120]), tries=item["tries"])
                        else:
                            self._write(item)
                    failed += 1
                    break                                  # the engine is probably not right yet: the next pass tries again
                self._deliver(item, text or "", frame, round((self.clock() - t0) * 1000))
                done += 1
                gap = self.cfg()["gap_s"]
                if gap:
                    self.sleep(gap)
        finally:
            self._busy.release()
        return {"ok": True, "processed": done, "failed": failed}

    def _deliver(self, item: Dict[str, Any], text: str, frame: bytes, ms: int) -> None:
        self._event("processed", item, ms=ms)
        handler = self.handlers.get(item["source"])
        if not handler:                                    # a source that pulls (the sentry): keep the result until it is acknowledged
            with self._lock:
                item.update(state="done", text=text, delivered_at=self.clock())
                self._write(item)
            return
        try:
            handler(item, text, frame)
            self._event("delivered_late", item)
        except Exception as e:
            self._event("failed", item, error="handler: %s: %s" % (type(e).__name__, str(e)[:120]))
        with self._lock:
            self._delete(item["id"])

    def results(self, source: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Finished items of a source that pulls, oldest first, with the frame (base64), until acknowledged."""
        with self._lock:
            self._expire()
            rows = sorted((i for i in self.items.values() if i["source"] == source and i["state"] == "done"), key=lambda i: i["id"])
        out = []
        for i in rows[:max(1, int(limit))]:
            try:
                frame = self._frame(i["id"])
            except OSError:
                continue
            out.append({"id": i["id"], "camera": i.get("camera"), "kind": i.get("kind"), "at": i["at"], "key": i.get("key"),
                        "meta": i.get("meta") or {}, "text": i.get("text", ""), "delivered_at": i.get("delivered_at"),
                        "frame_b64": base64.b64encode(frame).decode("ascii")})
        return out

    def ack(self, source: str, ids: List[str]) -> int:
        n = 0
        with self._lock:
            for item_id in ids or []:
                item = self.items.get(item_id)
                if item and item["source"] == source and item["state"] == "done":
                    self._event("delivered_late", item)
                    self._delete(item_id)
                    n += 1
        return n

    # ------------------------------------------------------------------ the loop ----
    def depth(self) -> int:
        with self._lock:
            return sum(1 for i in self.items.values() if i["state"] == "queued")

    def kick(self) -> None:
        """Right after a release: read what is waiting, on a thread of its own, without waiting for the next poll."""
        if self._kicking or not self.depth():
            return
        self._kicking = True

        def go() -> None:
            try:
                self.process()
            finally:
                self._kicking = False

        threading.Thread(target=go, name="vision-queue-kick", daemon=True).start()

    def tick(self) -> None:
        self.expire()
        if self.depth() and self.available_fn():
            self.process()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def loop() -> None:
            while True:
                try:
                    self.tick()
                except Exception:
                    pass
                self.sleep(self.cfg()["poll_s"])

        self._thread = threading.Thread(target=loop, name="vision-queue", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ status, metrics ----
    def status(self) -> Dict[str, Any]:
        with self._lock:
            rows = list(self.items.values())
        queued = [i for i in rows if i["state"] == "queued"]
        by: Dict[str, int] = {}
        for i in queued:
            by[i["source"]] = by.get(i["source"], 0) + 1
        return {"enabled": self.enabled(), "depth": len(queued), "waiting_for_pickup": sum(1 for i in rows if i["state"] == "done"),
                "by_source": by, "oldest_age_s": round(self.clock() - min(i["at"] for i in queued)) if queued else 0,
                "engine_available": bool(self.available_fn()), "max_age_h": self.cfg()["max_age_h"]}

    def metrics(self) -> List[str]:
        st = self.status()
        lines = ["# HELP stonesage_vision_queue_depth Frames waiting for the vision engine, by source.",
                 "# TYPE stonesage_vision_queue_depth gauge"]
        for src in sorted(set(list(st["by_source"]) + list(self.handlers) + ["sentry"])):
            lines.append('stonesage_vision_queue_depth{source="%s"} %d' % (src, st["by_source"].get(src, 0)))
        lines += ["# HELP stonesage_vision_queue_oldest_age_seconds Age of the oldest queued frame (0 = empty).",
                  "# TYPE stonesage_vision_queue_oldest_age_seconds gauge",
                  "stonesage_vision_queue_oldest_age_seconds %d" % st["oldest_age_s"],
                  "# HELP stonesage_vision_queue_events_total Queue events by kind (queued, processed, delivered_late, dropped_age, dropped_cap, failed).",
                  "# TYPE stonesage_vision_queue_events_total counter"]
        for (event, src), n in sorted(self.counts.items()):
            lines.append('stonesage_vision_queue_events_total{event="%s",source="%s"} %d' % (event, src, n))
        return lines
