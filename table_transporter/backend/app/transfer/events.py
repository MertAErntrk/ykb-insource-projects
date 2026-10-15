"""İş başına olay yayını (pipeline thread'i → async SSE aboneleri) ve SSE biçimi.

publish() herhangi bir thread'den çağrılır; her abone kendi event loop'undaki asyncio.Queue'ya
loop.call_soon_threadsafe ile alır. Terminal olay saklanır: sonradan abone olan onu hemen alır.
"""

import asyncio
import json
import logging
import threading

log = logging.getLogger(__name__)

TERMINAL_EVENTS = frozenset({"done", "failed", "cancelled"})
TERMINAL_STATUSES = frozenset({"done", "failed", "partial", "cancelled"})
PING_SEC = 15.0
_POLL_SEC = 1.0


class EventBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._terminal: tuple[str, dict] | None = None

    @property
    def terminal(self) -> tuple[str, dict] | None:
        return self._terminal

    def publish(self, name: str, data: dict) -> None:
        with self._lock:
            if self._terminal is not None:
                return  # terminalden sonra olay yok
            if name in TERMINAL_EVENTS:
                self._terminal = (name, data)
            subs = list(self._subs.items())
        for q, loop in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, (name, data))
            except RuntimeError:  # loop kapanmış: abone zaten gitmiş
                self.unsubscribe(q)

    def subscribe(self) -> asyncio.Queue:
        """Çalışan event loop içinden çağrılır."""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            if self._terminal is not None:
                q.put_nowait(self._terminal)
            else:
                self._subs[q] = loop
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.pop(q, None)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


def format_event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def state_event(job: dict) -> tuple[str, dict]:
    """Repo'daki iş kaydından güncel durum olayı (API.md SSE tablosu)."""
    jid, status = job["id"], job["status"]
    if status == "done":
        return "done", {"job_id": jid, "status": "done", "rows_written": job["rows_written"],
                        "finished_at": job["finished_at"],
                        "target": {"schema": job["target_schema"], "table": job["target_table"]}}
    if status in ("failed", "partial"):
        return "failed", {"job_id": jid, "status": status, "error": job["error_code"],
                          "detail": job["error_detail"], "rows_written": job["rows_written"]}
    if status == "cancelled":
        return "cancelled", {"job_id": jid, "status": "cancelled", "rows_written": job["rows_written"]}
    if status == "cancelling":
        return "cancelling", {"job_id": jid, "status": "cancelling"}
    return "progress", {"job_id": jid, "status": status, "rows_read": job["rows_read"],
                        "rows_written": job["rows_written"], "total_rows": job["total_rows"],
                        "rows_per_sec": job["rows_per_sec"], "eta_sec": job["eta_sec"]}


async def sse_stream(request, broker: EventBroker | None, load_job):
    """İlk olay güncel durum; sonra canlı olaylar; terminal olayda kapanır.
    load_job() → güncel iş kaydı (dict). Abonelik her çıkışta temizlenir."""
    q = broker.subscribe() if broker is not None else None
    try:
        job = load_job()
        name, data = state_event(job)
        if q is not None and broker.terminal is not None:
            name, data = broker.terminal  # yayın bitti; kayıttan daha kesin
            while not q.empty():
                q.get_nowait()
        yield format_event(name, data)
        if q is None or name in TERMINAL_EVENTS:
            return
        idle = 0.0
        while True:
            try:
                name, data = await asyncio.wait_for(q.get(), timeout=_POLL_SEC)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    return
                idle += _POLL_SEC
                if idle >= PING_SEC:
                    idle = 0.0
                    yield ": ping\n\n"
                continue
            idle = 0.0
            yield format_event(name, data)
            if name in TERMINAL_EVENTS:
                return
    finally:
        if q is not None:
            broker.unsubscribe(q)
