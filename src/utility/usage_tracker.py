"""Persistent LLM usage statistics, aggregation and cost estimation."""
import datetime
import os
import queue
import sqlite3
import threading
import time
import weakref
from typing import Callable

from gi.repository import GLib

PRICE_FIELDS = ("input", "output", "cache_read", "cache_write")
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")

INTERVALS = ("hour", "day", "week", "month")
RANGES = {
    "24h": datetime.timedelta(hours=24),
    "7d": datetime.timedelta(days=7),
    "30d": datetime.timedelta(days=30),
    "90d": datetime.timedelta(days=90),
    "365d": datetime.timedelta(days=365),
    "all": None,
}
DIMENSIONS = ("model", "provider", "pair", "workspace")
METRICS = ("tokens", "requests", "cost", "cache")
OTHER_KEY = "__other__"
MAX_BUCKETS = 400


def _int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


class UsageTracker:
    """Records one row per LLM generation in a local SQLite database.

    Recording happens on a dedicated worker thread so generation threads only
    pay for enqueuing, never for token estimation or disk writes.
    """

    def __init__(self):
        self.enabled = True
        self._path = None
        self._conn = None
        self._lock = threading.Lock()
        self._queue = queue.SimpleQueue()
        self._worker = None
        self._workspace_provider = None
        self._listeners = []
        self._local = threading.local()

    def configure(self, path: str, workspace_provider: Callable[[], tuple[str, str]] | None = None):
        with self._lock:
            if self._conn is not None and path != self._path:
                self._conn.close()
                self._conn = None
            self._path = path
        if workspace_provider is not None:
            self._workspace_provider = workspace_provider
        if self._worker is None:
            self._worker = threading.Thread(target=self._run, name="usage-tracker", daemon=True)
            self._worker.start()

    # -- Recording ----------------------------------------------------------

    def begin_generation(self, handler) -> bool:
        """Mark a generation as running; return False when nested in another one.

        Handlers may call each other's generation methods (``super()`` calls or
        generate_text delegating to generate_text_stream), which must count once.
        """
        active = getattr(self._local, "active", None)
        if active is None:
            active = self._local.active = set()
        if id(handler) in active:
            return False
        active.add(id(handler))
        return True

    def end_generation(self, handler):
        self._local.active.discard(id(handler))

    def submit(self, provider: str, model: str, usage: dict | None, response_text: str,
               prompt: str = "", history: list | None = None, system_prompt: list | None = None):
        if not self.enabled or self._path is None:
            return
        workspace_id, workspace_name = "", ""
        if self._workspace_provider is not None:
            try:
                workspace_id, workspace_name = self._workspace_provider()
            except Exception:
                pass
        self._queue.put({
            "ts": time.time(),
            "provider": provider or "",
            "model": model or "",
            "workspace_id": workspace_id or "",
            "workspace_name": workspace_name or "",
            "usage": dict(usage) if usage else {},
            "response_text": str(response_text or ""),
            "prompt": prompt if isinstance(prompt, str) else str(prompt or ""),
            "history": list(history or []),
            "system_prompt": list(system_prompt or []),
        })

    def _run(self):
        while True:
            item = self._queue.get()
            try:
                self._store(item)
            except Exception as error:
                print(f"Error recording LLM usage: {error}")
                continue
            self._schedule_notify()

    def _schedule_notify(self):
        # Headless runs have no listeners and may not iterate the main loop.
        if self._listeners:
            GLib.idle_add(self._notify)

    @staticmethod
    def _estimate_input(item) -> int:
        from .strings import count_tokens
        total = count_tokens(item["prompt"])
        for prompt in item["system_prompt"]:
            total += count_tokens(str(prompt))
        for message in item["history"]:
            if isinstance(message, dict):
                total += count_tokens(str(message.get("Message", "")))
        return total

    def _store(self, item):
        from .strings import count_tokens
        usage = item["usage"]
        estimated = False
        if usage.get("input_tokens") is None:
            input_tokens = self._estimate_input(item)
            estimated = True
        else:
            input_tokens = _int(usage.get("input_tokens"))
        if usage.get("output_tokens") is None:
            output_tokens = count_tokens(item["response_text"])
            estimated = True
        else:
            output_tokens = _int(usage.get("output_tokens"))
        values = (
            item["ts"], item["provider"], item["model"], item["workspace_id"], item["workspace_name"],
            input_tokens, output_tokens,
            _int(usage.get("cache_read_tokens")), _int(usage.get("cache_write_tokens")),
            _int(usage.get("reasoning_tokens")), int(estimated),
        )
        with self._lock:
            conn = self._connection()
            conn.execute(
                "INSERT INTO usage (ts, provider, model, workspace_id, workspace_name, input_tokens, "
                "output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, estimated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            conn.commit()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            # Several Newelle processes (e.g. the mini window) may share the file.
            conn = sqlite3.connect(self._path, timeout=10, check_same_thread=False)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS usage ("
                "id INTEGER PRIMARY KEY, ts REAL NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, "
                "workspace_id TEXT NOT NULL, workspace_name TEXT NOT NULL, "
                "input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, "
                "cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0, "
                "reasoning_tokens INTEGER NOT NULL DEFAULT 0, estimated INTEGER NOT NULL DEFAULT 0)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS usage_ts ON usage (ts)")
            conn.commit()
            self._conn = conn
        return self._conn

    # -- Listeners ----------------------------------------------------------

    def add_listener(self, callback):
        """Call ``callback`` on the main loop after each record. Bound methods are held weakly."""
        ref = weakref.WeakMethod(callback) if hasattr(callback, "__self__") else (lambda: callback)
        self._listeners.append(ref)

    def _notify(self):
        alive = []
        for ref in self._listeners:
            callback = ref()
            if callback is None:
                continue
            alive.append(ref)
            try:
                callback()
            except Exception as error:
                print(f"Usage listener error: {error}")
        self._listeners = alive
        return False

    # -- Queries ------------------------------------------------------------

    def query(self, start: float | None = None) -> list[dict]:
        """Return usage aggregated per hour, provider, model and workspace."""
        if self._path is None:
            return []
        sql = (
            "SELECT CAST(ts / 3600 AS INTEGER) * 3600 AS hour, provider, model, workspace_id, "
            "MAX(workspace_name), COUNT(*), SUM(input_tokens), SUM(output_tokens), "
            "SUM(cache_read_tokens), SUM(cache_write_tokens), SUM(reasoning_tokens), MAX(estimated), MIN(ts), "
            "SUM(CASE WHEN estimated = 0 THEN input_tokens ELSE 0 END) "
            "FROM usage {where} GROUP BY hour, provider, model, workspace_id ORDER BY hour"
        )
        params = ()
        where = ""
        if start is not None:
            where = "WHERE ts >= ?"
            params = (start,)
        with self._lock:
            rows = self._connection().execute(sql.format(where=where), params).fetchall()
        keys = ("hour", "provider", "model", "workspace_id", "workspace_name", "requests") + TOKEN_FIELDS + (
            "estimated", "first_ts", "reported_input_tokens",
        )
        return [dict(zip(keys, row)) for row in rows]

    def known_models(self) -> list[tuple[str, str]]:
        if self._path is None:
            return []
        with self._lock:
            rows = self._connection().execute(
                "SELECT provider, model FROM usage GROUP BY provider, model ORDER BY MAX(ts) DESC"
            ).fetchall()
        return [(provider, model) for provider, model in rows]

    def clear(self):
        if self._path is None:
            return
        with self._lock:
            conn = self._connection()
            conn.execute("DELETE FROM usage")
            conn.commit()
            conn.execute("VACUUM")
        self._schedule_notify()


_tracker = UsageTracker()


def get_usage_tracker() -> UsageTracker:
    return _tracker


# -- Cost --------------------------------------------------------------------

def get_price(prices: dict, provider: str, model: str) -> dict | None:
    """Return the price entry (per million tokens) for a provider/model pair, if any."""
    price = prices.get(provider, {}).get(model) if isinstance(prices.get(provider), dict) else None
    if not isinstance(price, dict):
        return None
    if price.get("input") is None and price.get("output") is None:
        return None
    return price


def compute_cost(row: dict, price: dict | None) -> float | None:
    """Cost of a usage row. Cache prices fall back to the input price when unset.

    ``input_tokens`` already includes cache reads and writes, so they are
    removed from the regular input before applying their own prices.
    """
    if price is None:
        return None
    input_price = price.get("input") or 0.0
    output_price = price.get("output") or 0.0
    cache_read_price = price.get("cache_read")
    cache_write_price = price.get("cache_write")
    if cache_read_price is None:
        cache_read_price = input_price
    if cache_write_price is None:
        cache_write_price = input_price
    cache_read = row["cache_read_tokens"]
    cache_write = row["cache_write_tokens"]
    uncached = max(0, row["input_tokens"] - cache_read - cache_write)
    return (
        uncached * input_price
        + cache_read * cache_read_price
        + cache_write * cache_write_price
        + row["output_tokens"] * output_price
    ) / 1_000_000


def compute_cache_savings(row: dict, price: dict | None) -> float | None:
    """Net amount saved by prompt caching compared with paying the input price.

    Cache writes priced above the input price (e.g. Anthropic) reduce the
    savings. Returns ``None`` when no cache price is set, since cache tokens
    then fall back to the input price and nothing can be saved.
    """
    if price is None or (price.get("cache_read") is None and price.get("cache_write") is None):
        return None
    input_price = price.get("input") or 0.0
    cache_read_price = price.get("cache_read")
    cache_write_price = price.get("cache_write")
    if cache_read_price is None:
        cache_read_price = input_price
    if cache_write_price is None:
        cache_write_price = input_price
    return (
        row["cache_read_tokens"] * (input_price - cache_read_price)
        + row["cache_write_tokens"] * (input_price - cache_write_price)
    ) / 1_000_000


# -- Aggregation -------------------------------------------------------------

def bucket_start(moment: datetime.datetime, interval: str) -> datetime.datetime:
    if interval == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if interval == "day":
        return day
    if interval == "week":
        return day - datetime.timedelta(days=day.weekday())
    return day.replace(day=1)


def next_bucket(moment: datetime.datetime, interval: str) -> datetime.datetime:
    if interval == "hour":
        return moment + datetime.timedelta(hours=1)
    if interval == "day":
        return moment + datetime.timedelta(days=1)
    if interval == "week":
        return moment + datetime.timedelta(days=7)
    if moment.month == 12:
        return moment.replace(year=moment.year + 1, month=1)
    return moment.replace(month=moment.month + 1)


def auto_interval(span: datetime.timedelta) -> str:
    if span <= datetime.timedelta(days=2):
        return "hour"
    if span <= datetime.timedelta(days=62):
        return "day"
    if span <= datetime.timedelta(days=200):
        return "week"
    return "month"


def range_start(range_key: str, now: datetime.datetime) -> datetime.datetime | None:
    delta = RANGES.get(range_key)
    return None if delta is None else now - delta


def _bucket_list(start: datetime.datetime, end: datetime.datetime, interval: str) -> list[datetime.datetime]:
    buckets = []
    current = bucket_start(start, interval)
    while current <= end:
        buckets.append(current)
        current = next_bucket(current, interval)
    return buckets


def cache_hit_rate(cache_read_tokens: float, reported_input_tokens: float) -> float | None:
    """Percentage of input tokens served from the provider's prompt cache.

    Only provider-reported input counts: estimated rows carry no cache
    information and would otherwise drag the rate towards zero.
    """
    if reported_input_tokens <= 0:
        return None
    return min(100.0, cache_read_tokens / reported_input_tokens * 100)


def group_key(row: dict, dimension: str):
    if dimension == "model":
        return row["model"]
    if dimension == "provider":
        return row["provider"]
    if dimension == "workspace":
        return row["workspace_id"]
    return (row["provider"], row["model"])


def aggregate(rows: list[dict], range_key: str, interval: str, dimension: str, metric: str,
              prices: dict, max_series: int = 6, now: datetime.datetime | None = None) -> dict:
    """Bucket hourly rows into a time series grouped by ``dimension``.

    ``interval`` may be "auto"; it is also coarsened automatically when the
    range would otherwise produce more than MAX_BUCKETS bars. For the
    "cache" metric, series values are hit-rate percentages (``None`` where a
    bucket has no reported input) and ``overall`` holds the combined rate.
    """
    now = now or datetime.datetime.now()
    start = range_start(range_key, now)
    if start is None:
        first = min((row["first_ts"] for row in rows), default=None)
        start = datetime.datetime.fromtimestamp(first) if first is not None else now - datetime.timedelta(days=7)
    if interval not in INTERVALS:
        interval = auto_interval(now - start)
    buckets = _bucket_list(start, now, interval)
    while len(buckets) > MAX_BUCKETS and interval != "month":
        interval = INTERVALS[INTERVALS.index(interval) + 1]
        buckets = _bucket_list(start, now, interval)
    bucket_index = {bucket: index for index, bucket in enumerate(buckets)}

    groups = {}
    totals = {"requests": 0, "cost": 0.0, "estimated": False, "unpriced": set(), "priced": False,
              "reported_input_tokens": 0, "cache_savings": 0.0, "cache_savings_priced": False}
    for field in TOKEN_FIELDS:
        totals[field] = 0
    overall_hits = [0] * len(buckets)
    overall_inputs = [0] * len(buckets)
    for row in rows:
        price = get_price(prices, row["provider"], row["model"])
        cost = compute_cost(row, price)
        savings = compute_cache_savings(row, price)
        if savings is not None:
            totals["cache_savings"] += savings
            totals["cache_savings_priced"] = True
        key = group_key(row, dimension)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "key": key, "requests": 0, "cost": 0.0, "priced": False, "unpriced": False,
                "estimated": False, "values": [0.0] * len(buckets),
                "cache_hits": [0] * len(buckets), "cache_inputs": [0] * len(buckets),
                "reported_input_tokens": 0,
                "workspace_name": row["workspace_name"], "provider": row["provider"], "model": row["model"],
            }
            for field in TOKEN_FIELDS:
                group[field] = 0
        group["requests"] += row["requests"]
        totals["requests"] += row["requests"]
        reported_input = row.get("reported_input_tokens") or 0
        group["reported_input_tokens"] += reported_input
        totals["reported_input_tokens"] += reported_input
        for field in TOKEN_FIELDS:
            group[field] += row[field] or 0
            totals[field] += row[field] or 0
        group["estimated"] = group["estimated"] or bool(row["estimated"])
        totals["estimated"] = totals["estimated"] or bool(row["estimated"])
        if cost is None:
            group["unpriced"] = True
            totals["unpriced"].add((row["provider"], row["model"]))
        else:
            group["priced"] = True
            totals["priced"] = True
            group["cost"] += cost
            totals["cost"] += cost
        if row["workspace_name"]:
            group["workspace_name"] = row["workspace_name"]

        moment = datetime.datetime.fromtimestamp(row["hour"])
        index = bucket_index.get(bucket_start(moment, interval))
        if index is None:
            # Hour rows are aligned to UTC, so with half-hour time zones the
            # first one can start slightly before the first local bucket.
            if buckets and moment < buckets[0]:
                index = 0
            else:
                continue
        cache_read = row["cache_read_tokens"] or 0
        group["cache_hits"][index] += cache_read
        group["cache_inputs"][index] += reported_input
        overall_hits[index] += cache_read
        overall_inputs[index] += reported_input
        if metric == "requests":
            value = row["requests"]
        elif metric == "cost":
            value = cost or 0.0
        else:
            value = (row["input_tokens"] or 0) + (row["output_tokens"] or 0)
        group["values"][index] += value

    for group in groups.values():
        group["cache_hit_rate"] = cache_hit_rate(group["cache_read_tokens"], group["reported_input_tokens"])
        if metric == "cache":
            group["values"] = [
                cache_hit_rate(hits, inputs) for hits, inputs in zip(group["cache_hits"], group["cache_inputs"])
            ]
    totals["cache_hit_rate"] = cache_hit_rate(totals["cache_read_tokens"], totals["reported_input_tokens"])

    def metric_total(group):
        if metric == "requests":
            return group["requests"]
        if metric == "cost":
            return group["cost"]
        if metric == "cache":
            # Rank by cache-eligible volume, so the lines shown matter most.
            return group["reported_input_tokens"]
        return group["input_tokens"] + group["output_tokens"]

    ordered = sorted(groups.values(), key=lambda group: (metric_total(group), group["requests"]), reverse=True)
    nonzero = [group for group in ordered if metric_total(group) > 0]
    series = nonzero[:max_series]
    rest = nonzero[max_series:]
    if rest:
        if len(rest) == 1:
            series.append(rest[0])
        elif metric == "cache":
            other_values = [
                cache_hit_rate(
                    sum(group["cache_hits"][index] for group in rest),
                    sum(group["cache_inputs"][index] for group in rest),
                )
                for index in range(len(buckets))
            ]
            series.append({"key": OTHER_KEY, "values": other_values, "count": len(rest)})
        else:
            other_values = [sum(group["values"][index] for group in rest) for index in range(len(buckets))]
            series.append({"key": OTHER_KEY, "values": other_values, "count": len(rest)})
    totals["unpriced"] = len(totals["unpriced"])
    overall = None
    if metric == "cache":
        overall = [cache_hit_rate(hits, inputs) for hits, inputs in zip(overall_hits, overall_inputs)]
    return {
        "buckets": buckets,
        "interval": interval,
        "series": series,
        "groups": ordered,
        "totals": totals,
        "overall": overall,
        "metric": metric,
        "dimension": dimension,
    }
