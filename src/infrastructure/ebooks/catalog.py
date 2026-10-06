"""Search and download free, legal ebooks for the ``search_ebooks`` /
``download_ebook`` MCP tools.

Sources (all public domain or openly licensed):
- **Project Gutenberg** via its own OPDS search (Gutendex was 26-40s+ per call).
- **Standard Ebooks** — no public JSON API (the OPDS feed is patrons-only, 401),
  so search scrapes the ``schema:Book`` microdata on /ebooks?query=, and the
  download link is read off the book page. A bare ``…/downloads/x.epub`` returns a
  "thank you" HTML page with a meta refresh; ``?source=download`` gets the file.
- **Internet Archive**, restricted to library-scan collections that are public
  domain (``americana``, ``toronto``, …) and not lending-only. User uploads
  (``opensource`` etc.) are excluded on purpose: plenty of them are pirated.

The agent never passes a URL. It passes a ``book_id`` (``gutenberg:1342``,
``se:jane-austen/pride-and-prejudice``, ``ia:<identifier>``); we build the URL
and every request — redirects included — must stay on ``_ALLOWED_HOSTS``. That
stops a prompt-injected instruction from turning this into a generic fetcher.
"""

from __future__ import annotations

import contextlib
import html
import re
import secrets
from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import anyio
import httpx

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_USER_AGENT = "gendan-telegram-bot/1.0 (+https://github.com/quango2304/telegram_agy)"
_ALLOWED_HOSTS = ("gutenberg.org", "standardebooks.org", "archive.org")

# Public-domain library scans only — see the module docstring.
_IA_COLLECTIONS = "(americana OR toronto OR europeanlibraries OR cdl OR gutenberg)"

_GUTENBERG_FORMATS = {
    "epub": ("epub3.images", "epub"),
    "azw3": ("kf8.images", "azw3"),
    "txt": ("txt.utf-8", "txt"),
}
_SE_FORMATS = {"epub": ".epub", "azw3": ".azw3", "kepub": ".kepub.epub"}
_IA_FORMATS = {"epub": ("EPUB",), "pdf": ("Text PDF", "Image Container PDF")}

_SE_SLUG = re.compile(r"[a-z0-9-]+(?:/[a-z0-9_-]+)+")
_IA_ID = re.compile(r"[A-Za-z0-9._-]{1,100}")


class EbookError(Exception):
    """User-facing failure (bad id, not found, too big, source down)."""


@dataclass
class Book:
    book_id: str
    title: str
    authors: str
    source: str
    formats: list[str] = field(default_factory=list)
    language: str = ""
    year: str = ""

    def line(self) -> str:
        bits = [f"`{self.book_id}`", self.title]
        if self.authors:
            bits.append(f"— {self.authors}")
        meta = [self.source]
        if self.language:
            meta.append(self.language)
        if self.year:
            meta.append(self.year)
        if self.formats:
            meta.append("/".join(self.formats))
        return " ".join(bits) + f" [{', '.join(meta)}]"


async def _check_host(request: httpx.Request) -> None:
    host = request.url.host
    if not any(host == h or host.endswith("." + h) for h in _ALLOWED_HOSTS):
        raise EbookError(f"Chặn request tới host lạ: {host}")
    if request.url.scheme != "https":
        raise EbookError("Chỉ cho phép https.")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
        event_hooks={"request": [_check_host]},
    )


# --- search -----------------------------------------------------------------


_PG_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
_PG_ID = re.compile(r'href="/ebooks/(\d+)\.opds"')


def _xml_text(tag: str, body: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", body, re.S)
    return html.unescape(m.group(1)).strip() if m else ""


async def _search_gutenberg(client: httpx.AsyncClient, query: str, lang: str) -> list[Book]:
    # Gutenberg's own OPDS search (~1s). Gutendex was 26-40s+ and timed out.
    # `l.<code>` is Gutenberg's in-query language filter.
    q = f"{query} l.{lang}" if lang else query
    r = await client.get("https://www.gutenberg.org/ebooks/search.opds/", params={"query": q})
    r.raise_for_status()
    out = []
    for body in _PG_ENTRY.findall(r.text):
        m = _PG_ID.search(body)
        if not m:
            continue  # navigation entries ("sort by…") have no book id
        out.append(
            Book(
                book_id=f"gutenberg:{m.group(1)}",
                title=_xml_text("title", body) or "?",
                authors=_xml_text("content", body),
                source="Gutenberg",
                formats=list(_GUTENBERG_FORMATS),
                language=lang,
            )
        )
    return out


_SE_ITEM = re.compile(r'<li typeof="schema:Book" about="/ebooks/([^"]+)"(.*?)</li>', re.S)
_SE_NAME = re.compile(r'property="schema:name"[^>]*>([^<]+)<')


async def _search_standard_ebooks(client: httpx.AsyncClient, query: str, lang: str) -> list[Book]:
    if lang and lang != "en":
        return []  # Standard Ebooks is English-only.
    r = await client.get("https://standardebooks.org/ebooks", params={"query": query})
    r.raise_for_status()
    out = []
    for slug, body in _SE_ITEM.findall(r.text):
        names = [html.unescape(n).strip() for n in _SE_NAME.findall(body)]
        out.append(
            Book(
                book_id=f"se:{slug}",
                title=names[0] if names else slug,
                authors="; ".join(names[1:]),
                source="Standard Ebooks",
                formats=list(_SE_FORMATS),
                language="en",
            )
        )
    return out


async def _search_internet_archive(
    client: httpx.AsyncClient, query: str, lang: str, rows: int
) -> list[Book]:
    q = (
        f"({query}) AND mediatype:texts AND collection:{_IA_COLLECTIONS} "
        'AND format:(EPUB OR "Text PDF") AND NOT access-restricted-item:true'
    )
    if lang:
        q += f" AND language:({lang})"
    params = [("q", q), ("rows", str(rows)), ("output", "json")]
    fields = ("identifier", "title", "creator", "year", "language", "format")
    params += [("fl[]", f) for f in fields]
    r = await client.get("https://archive.org/advancedsearch.php", params=tuple(params))
    r.raise_for_status()
    out = []
    for d in r.json().get("response", {}).get("docs", []):
        have = set(_as_list(d.get("format")))
        fmts = [f for f, names in _IA_FORMATS.items() if have.intersection(names)]
        out.append(
            Book(
                book_id=f"ia:{d['identifier']}",
                title=str(d.get("title", "?")),
                authors="; ".join(_as_list(d.get("creator"))),
                source="Internet Archive",
                formats=fmts,
                language=",".join(_as_list(d.get("language"))),
                year=str(d.get("year", "")),
            )
        )
    return out


def _as_list(v: object) -> list[str]:
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


async def search(query: str, lang: str = "", limit: int = 10) -> tuple[list[Book], list[str]]:
    """Search every source concurrently. Returns (books, per-source errors).

    One source being down must not sink the others, so each failure is reported
    back as a string instead of raised."""
    query = query.strip()
    if not query:
        raise EbookError("Thiếu từ khoá.")
    lang = lang.strip().lower()
    results: dict[str, list[Book]] = {}
    errors: list[str] = []

    async with _client() as client:

        async def run(name: str, coro: Awaitable[list[Book]]) -> None:
            try:
                results[name] = (await coro)[:limit]
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {type(exc).__name__}")

        async with anyio.create_task_group() as tg:
            tg.start_soon(run, "Gutenberg", _search_gutenberg(client, query, lang))
            tg.start_soon(run, "Standard Ebooks", _search_standard_ebooks(client, query, lang))
            tg.start_soon(
                run, "Internet Archive", _search_internet_archive(client, query, lang, limit)
            )

    # Standard Ebooks first (best-formatted), then Gutenberg, then IA scans.
    order = ("Standard Ebooks", "Gutenberg", "Internet Archive")
    books = [b for name in order for b in results.get(name, [])]
    return books, errors


# --- download ---------------------------------------------------------------


def _safe_name(title: str, ext: str) -> str:
    stem = re.sub(r"[^\w\- ]+", "", title, flags=re.UNICODE).strip().replace(" ", "_")[:80]
    return f"{stem or 'ebook'}_{secrets.token_hex(3)}.{ext}"


async def _resolve_gutenberg(client: httpx.AsyncClient, ref: str, fmt: str) -> tuple[str, str]:
    if not ref.isdigit():
        raise EbookError("book_id Gutenberg phải là số, vd gutenberg:1342.")
    if fmt not in _GUTENBERG_FORMATS:
        raise EbookError(f"Gutenberg có: {', '.join(_GUTENBERG_FORMATS)}.")
    suffix, ext = _GUTENBERG_FORMATS[fmt]
    title = f"gutenberg_{ref}"
    # Title is cosmetic (file name only); never let it fail the download.
    with contextlib.suppress(Exception):
        r = await client.get(f"https://www.gutenberg.org/ebooks/{ref}.opds", timeout=8.0)
        title = re.sub(r" by .*$", "", _xml_text("title", r.text)) or title
    return f"https://www.gutenberg.org/ebooks/{ref}.{suffix}", _safe_name(title, ext)


async def _resolve_standard_ebooks(
    client: httpx.AsyncClient, ref: str, fmt: str
) -> tuple[str, str]:
    if not _SE_SLUG.fullmatch(ref):
        raise EbookError("book_id Standard Ebooks không hợp lệ.")
    if fmt not in _SE_FORMATS:
        raise EbookError(f"Standard Ebooks có: {', '.join(_SE_FORMATS)}.")
    r = await client.get(f"https://standardebooks.org/ebooks/{ref}")
    if r.status_code == 404:
        raise EbookError("Không thấy sách này trên Standard Ebooks.")
    r.raise_for_status()
    ext = _SE_FORMATS[fmt]
    # The plain .epub link must not match .kepub.epub / _advanced.epub.
    links = re.findall(rf'href="(/ebooks/{re.escape(ref)}/downloads/[^"]+)"', r.text)
    picks = [
        u
        for u in links
        if u.endswith(ext)
        and not (fmt == "epub" and (u.endswith(".kepub.epub") or "_advanced" in u))
    ]
    if not picks:
        raise EbookError(f"Sách này không có bản {fmt}.")
    m = re.search(r'<h1 property="schema:name">([^<]+)<', r.text)
    title = html.unescape(m.group(1)) if m else ref.split("/")[1]
    url = f"https://standardebooks.org{picks[0]}?source=download"
    return url, _safe_name(title, ext.lstrip(".").replace(".", "_"))


async def _resolve_internet_archive(
    client: httpx.AsyncClient, ref: str, fmt: str, max_bytes: int
) -> tuple[str, str]:
    if not _IA_ID.fullmatch(ref):
        raise EbookError("book_id Internet Archive không hợp lệ.")
    if fmt not in _IA_FORMATS:
        raise EbookError(f"Internet Archive có: {', '.join(_IA_FORMATS)}.")
    r = await client.get(f"https://archive.org/metadata/{ref}")
    r.raise_for_status()
    data = r.json()
    meta = data.get("metadata") or {}
    if not meta:
        raise EbookError("Không thấy item này trên Internet Archive.")
    # Re-check on download, not just search: the id could have come from anywhere.
    colls = set(_as_list(meta.get("collection")))
    allowed = {"americana", "toronto", "europeanlibraries", "cdl", "gutenberg"}
    if not colls & allowed or meta.get("access-restricted-item") in (True, "true"):
        raise EbookError("Item này không thuộc bộ sưu tập public domain được phép tải.")
    files = [
        f
        for f in data.get("files", [])
        if f.get("format") in _IA_FORMATS[fmt] and str(f.get("private", "")).lower() != "true"
    ]
    if not files:
        raise EbookError(f"Item này không có bản {fmt}.")
    files.sort(key=lambda f: int(f.get("size") or 0))
    pick = files[0]
    if int(pick.get("size") or 0) > max_bytes:
        raise EbookError(f"File {int(pick['size']) // (1024 * 1024)}MB, quá giới hạn.")
    title = str(meta.get("title") or ref)
    url = f"https://archive.org/download/{ref}/{quote(pick['name'])}"
    return url, _safe_name(title, "pdf" if fmt == "pdf" else "epub")


async def download(book_id: str, fmt: str, outbox: str, max_mb: int) -> Path:
    """Download ``book_id`` in ``fmt`` into ``outbox``; return the file path.

    Streams with a hard byte cap and writes to a ``.part`` file first, so a
    half-downloaded book never sits in /outbox looking complete."""
    source, _, ref = book_id.strip().partition(":")
    fmt = (fmt or "epub").strip().lower()
    max_bytes = max_mb * 1024 * 1024
    async with _client() as client:
        if source == "gutenberg":
            url, name = await _resolve_gutenberg(client, ref, fmt)
        elif source == "se":
            url, name = await _resolve_standard_ebooks(client, ref, fmt)
        elif source == "ia":
            url, name = await _resolve_internet_archive(client, ref, fmt, max_bytes)
        else:
            raise EbookError("book_id phải bắt đầu bằng gutenberg:, se: hoặc ia:.")

        dest = anyio.Path(outbox) / name
        part = anyio.Path(f"{dest}.part")
        written = 0
        try:
            async with client.stream("GET", url) as resp:
                if resp.status_code == 404:
                    raise EbookError("Nguồn báo không có file này (404).")
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                if ctype.startswith("text/html") or "xhtml" in ctype:
                    raise EbookError("Nguồn trả về trang web thay vì file sách.")
                async with await anyio.open_file(part, "wb") as fh:
                    async for chunk in resp.aiter_bytes(64 * 1024):
                        written += len(chunk)
                        if written > max_bytes:
                            raise EbookError(f"File vượt {max_mb}MB, dừng tải.")
                        await fh.write(chunk)
            await part.rename(dest)
        except BaseException:
            await part.unlink(missing_ok=True)
            raise
    return Path(dest)
