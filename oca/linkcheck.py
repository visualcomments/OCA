"""Optional network link checker for OCA.

Why this is a separate module
-----------------------------
`oca.scanner.oca_scan.scan()` is deterministic and offline by design: the
same repository must always produce the same JSON, which is what makes
`oca diff` meaningful and lets a score be trusted. Fetching URLs would
break that — a link that is up today may be down tomorrow, and a slow
network would change results run to run.

So link checking lives here, runs only when asked (`oca linkcheck`), and
caches every answer on disk. The cache is the reproducibility mechanism:
a second run with a warm cache produces identical output without touching
the network.

A second trap: a "broken" verdict is easy to get wrong. Plenty of servers
answer a legitimate page with 403 (bot protection), 405 (HEAD not allowed)
or 429 (rate limiting) — none of which mean the link is dead. Those are
classified as `blocked`, not `broken`, and never reported as defects.

Usage (via CLI):
    oca linkcheck <repo> [--timeout 10] [--workers 8] [--cache DIR]
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_TIMEOUT = 10
DEFAULT_WORKERS = 8
# Be a polite client: identify honestly so an admin can contact us.
USER_AGENT = (
    "OCA-linkcheck/0.1 (+https://github.com/visualcomments/OCA; "
    "checks course links; respects 429)"
)

# Statuses that mean "the link is fine, we were just not allowed to see it".
# Treating these as broken is the classic false positive of link checkers.
BLOCKED_STATUSES = {401, 403, 405, 406, 429, 503}
# doi.org and friends answer with redirects to publisher pages.
OK_STATUSES = set(range(200, 400))

# Domains that are routinely unreachable from CI sandboxes and some
# countries while being perfectly valid for the reader: messengers, social
# networks, and local file hosts. A network-level failure on one of these
# says something about OUR connectivity, not about the course.
SANDBOX_BLOCKED_HOSTS = (
    "t.me", "telegram.org", "telegram.me", "vk.com", "ok.ru",
    "disk.yandex.ru", "disk.yandex.com", "yadi.sk",
    "colab.research.google.com", "drive.google.com", "docs.google.com",
    "youtube.com", "youtu.be",
)
# Error substrings that indicate our own network, not a dead remote page.
LOCAL_NETWORK_ERRORS = (
    "network is unreachable", "temporary failure in name resolution",
    "name or service not known", "connection refused", "no route to host",
    "network unreachable",
)


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host


def _is_sandbox_limited(url: str, error_text: str) -> bool:
    """True when the failure likely describes our environment, not the link."""
    host = _host_of(url)
    if any(host == h or host.endswith("." + h) for h in SANDBOX_BLOCKED_HOSTS):
        return True
    low = error_text.lower()
    return any(marker in low for marker in LOCAL_NETWORK_ERRORS)


def _cache_key(url: str) -> str:
    import hashlib
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


class LinkCache:
    """On-disk cache so repeated runs are deterministic and fast."""

    def __init__(self, directory: Path, ttl_days: int = 30):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_days * 86400
        self.hits = 0
        self.misses = 0

    def get(self, url: str) -> dict | None:
        p = self.dir / f"{_cache_key(url)}.json"
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if self.ttl and time.time() - data.get("checked_at", 0) > self.ttl:
            return None
        self.hits += 1
        return data

    def put(self, url: str, result: dict) -> None:
        self.misses += 1
        p = self.dir / f"{_cache_key(url)}.json"
        payload = dict(result, url=url, checked_at=time.time())
        try:
            p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def check_url(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Fetch one URL and classify the outcome.

    Returns a dict with `status` (ok / broken / blocked / error) plus the
    HTTP code and a human-readable reason.
    """
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": "ok", "code": resp.status, "reason": ""}
    except urllib.error.HTTPError as e:
        if e.code in OK_STATUSES:
            return {"status": "ok", "code": e.code, "reason": ""}
        if e.code in BLOCKED_STATUSES:
            return {"status": "blocked", "code": e.code,
                    "reason": f"сервер ответил {e.code} — доступ ограничен, не обязательно битая ссылка"}
        if e.code == 404 or e.code == 410:
            return {"status": "broken", "code": e.code, "reason": f"HTTP {e.code} — страница не найдена"}
        return {"status": "broken", "code": e.code, "reason": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e))
        # A certificate failure is about OUR trust store, not the link: old
        # university sites frequently use a CA that modern Python does not
        # ship. Reported separately so nobody "fixes" a working link.
        if "CERTIFICATE_VERIFY_FAILED" in reason or "certificate verify failed" in reason.lower():
            return {"status": "tls", "code": None,
                    "reason": "сертификат не проверен (проблема доверия, не ссылки)"}
        if _is_sandbox_limited(url, reason):
            # Our sandbox (or this network) cannot reach the host. That is a
            # fact about us; reporting it as a broken course link would be
            # wrong, and would push authors to "fix" links that work fine.
            return {"status": "unreachable", "code": None,
                    "reason": f"хост недоступен из этой сети: {reason[:100]}"}
        # A genuine DNS failure on an ordinary domain means a typo in the URL.
        return {"status": "broken", "code": None, "reason": f"сеть: {reason[:120]}"}
    except (TimeoutError, OSError) as e:
        text = str(e)
        if _is_sandbox_limited(url, text):
            return {"status": "unreachable", "code": None,
                    "reason": f"хост недоступен из этой сети: {text[:100]}"}
        return {"status": "error", "code": None, "reason": f"таймаут/ошибка: {text[:120]}"}


def check_links(
    external: list[dict],
    cache_dir: Path,
    timeout: int = DEFAULT_TIMEOUT,
    workers: int = DEFAULT_WORKERS,
    offline: bool = False,
) -> dict:
    """Check a list of {file, url} records, using and filling the cache.

    `offline=True` answers purely from cache: uncached URLs are reported as
    `unknown` rather than fetched, which makes a fully cached run usable in
    CI without any network access.
    """
    cache = LinkCache(cache_dir)

    # Deduplicate: the surveyed courses repeat the same URLs many times
    # (openalex.org alone appeared 900 times), so check each once.
    unique: dict[str, dict] = {}
    for rec in external:
        unique.setdefault(rec["url"], rec)

    results: dict[str, dict] = {}
    to_fetch: list[str] = []
    for url in unique:
        hit = cache.get(url)
        if hit:
            results[url] = hit
        elif offline:
            results[url] = {"status": "unknown", "code": None,
                            "reason": "нет в кэше (офлайн-режим)"}
        else:
            to_fetch.append(url)

    if to_fetch:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for url, res in zip(to_fetch, pool.map(lambda u: check_url(u, timeout), to_fetch)):
                results[url] = res
                cache.put(url, res)

    by_status: dict[str, list[dict]] = {}
    for url, res in results.items():
        rec = dict(unique[url])
        rec.update(res)
        by_status.setdefault(res["status"], []).append(rec)

    return {
        "checked": len(results),
        "unique_urls": len(unique),
        "by_status": {k: len(v) for k, v in by_status.items()},
        "results": by_status,
        "cache": {"dir": str(cache.dir), "hits": cache.hits,
                  "misses": cache.misses, "offline": offline},
    }


def render(report: dict) -> str:
    """Human-readable link-check summary."""
    L: list[str] = []
    counts = report["by_status"]
    L.append("OCA linkcheck")
    L.append("=" * 72)
    L.append(f"Уникальных ссылок: {report['unique_urls']} | проверено: {report['checked']}")
    c = report["cache"]
    L.append(f"Кэш: {c['dir']} (попаданий {c['hits']}, запросов {c['misses']}"
             + (", офлайн" if c["offline"] else "") + ")")
    L.append("")
    labels = {"ok": "работают", "broken": "БИТЫЕ", "blocked": "доступ ограничен",
              "unreachable": "недоступны из этой сети", "tls": "сертификат не проверен",
              "error": "ошибка/таймаут", "unknown": "нет данных"}
    for status in ("ok", "broken", "blocked", "unreachable", "tls", "error", "unknown"):
        if counts.get(status):
            L.append(f"  {labels[status]:<24} {counts[status]}")

    for status, title in (("broken", "Битые ссылки"),
                          ("error", "Не удалось проверить"),
                          ("blocked", "Доступ ограничен (не считается дефектом)"),
                          ("unreachable", "Недоступны из этой сети (не считается дефектом)"),
                          ("tls", "Сертификат не проверен (не считается дефектом)")):
        items = report["results"].get(status) or []
        if not items:
            continue
        L.append(f"\n[{title}]  {len(items)}")
        for r in items[:30]:
            L.append(f"  • {r['url'][:88]}")
            L.append(f"      {r['reason']}  ({r['file']})")
        if len(items) > 30:
            L.append(f"  …и ещё {len(items) - 30}")
    if not report["results"].get("broken"):
        L.append("\nБитых ссылок не найдено.")
    return "\n".join(L)
