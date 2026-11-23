"""Checks GitHub for a newer release.

Self-contained on purpose: no Qt and nothing rewnd-specific, so the same file can drop into any
app that ships through GitHub releases. Give it the repo and the running version; it hands back the
newer release, or None. It never raises: no internet, a GitHub hiccup or rate limiting all just
mean "no update found this time".
"""

import json
import os
import re
import ssl
import urllib.request
from dataclasses import dataclass

DISABLE_ENV = "REWND_NO_UPDATE_CHECK"


@dataclass
class Release:
    version: str     # "1.0.2", without the leading v
    tag: str         # "v1.0.2"
    url: str         # the release page, where the downloads are
    name: str = ""
    summary: str = ""  # first real line of the release notes, plain text


def parse_version(v):
    """'v1.2.10' -> (1, 2, 10). Anything after a hyphen (1.2.0-beta) is ignored."""
    core = str(v).strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    nums = [int(n) for n in re.findall(r"\d+", core)]
    while len(nums) > 1 and nums[-1] == 0:   # 1.0 == 1.0.0
        nums.pop()
    return tuple(nums)


def is_newer(latest, current):
    a, b = parse_version(latest), parse_version(current)
    return bool(a) and a > b


def _plain(markdown):
    """First line of release notes that reads as a sentence, with markdown stripped."""
    for line in (markdown or "").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "- ", "* ", "|", ">", "<!--")):
            continue   # headings, list items, tables, quotes, comments
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)   # links
        line = re.sub(r"[*_`]", "", line)
        if len(line) > 160:
            line = line[:157].rstrip() + "…"
        return line
    return ""


def _context():
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs():   # frozen builds on some Linux distros can't find the system store
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except Exception:
            pass
    return ctx


def fetch_latest(repo, timeout=6.0):
    """The newest published, non-prerelease release of owner/name, or None."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"{repo.split('/')[-1]}-update-check"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_context()) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    tag = data.get("tag_name") or ""
    if not tag or data.get("draft") or data.get("prerelease"):
        return None
    return Release(
        version=tag.lstrip("vV"),
        tag=tag,
        url=data.get("html_url") or f"https://github.com/{repo}/releases/latest",
        name=data.get("name") or tag,
        summary=_plain(data.get("body")),
    )


def check(repo, current, skip=None, fetch=None):
    """A Release if one newer than `current` (and not the skipped version) is out, else None."""
    if os.environ.get(DISABLE_ENV) == "1":
        return None
    rel = (fetch or fetch_latest)(repo)
    if rel is None or not is_newer(rel.version, current):
        return None
    if skip and parse_version(skip) == parse_version(rel.version):
        return None
    return rel
