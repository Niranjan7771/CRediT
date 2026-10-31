"""Fetch the current CRediT taxonomy and its definitions."""
import html as html_lib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


CREDIT_URL = "https://credit.niso.org/"

# In-memory cache for live-fetched CRediT roles across calls in the same process
_ROLES_CACHE = None



def fetch_credit_roles(use_cache=True):
    """
    Dynamically fetch the 14 official CRediT contributor roles and definitions
    directly from credit.niso.org at runtime.

    Zero hardcoding: all role names, URLs, definitions, and task examples are
    harvested directly from the official website.

    Returns:
        list of dicts: [{'name': str, 'url': str, 'definition': str}, ...]
        or None on network failure.
    """
    global _ROLES_CACHE
    if use_cache and _ROLES_CACHE is not None:
        return _ROLES_CACHE

    try:
        logger.info("Fetching live CRediT roles from %s ...", CREDIT_URL)
        req = Request(CREDIT_URL, headers={"User-Agent": "CRediT-Parser/2.0"})
        with urlopen(req, timeout=20) as resp:
            page_html = resp.read().decode("utf-8")

        # Extract role links: <a href="https://credit.niso.org/contributor-roles/slug/">Name</a>
        pattern = (
            r'<a[^>]+href="(https://credit\.niso\.org/contributor-roles/[^"/]+/?)"'
            r'[^>]*>([^<]+)</a>'
        )
        matches = re.findall(pattern, page_html)

        roles = []
        seen = set()
        for url, raw_name in matches:
            name = html_lib.unescape(raw_name).strip()
            # Normalize dashes in role names to standard representation
            name = name.replace("\u2013", "–").replace("\u2014", "–")
            key = name.lower()
            if key not in seen and len(name) > 3:
                seen.add(key)
                roles.append({"name": name, "url": url, "definition": ""})

        if len(roles) != 14:
            logger.error("Expected exactly 14 live CRediT roles, found %d", len(roles))
            return None

        logger.info("Successfully fetched %d CRediT roles from landing page", len(roles))

        # Concurrently fetch definitions and task examples for each role
        _fetch_definitions_concurrently(roles)
        if any(not role["definition"] for role in roles):
            logger.error("One or more live CRediT role definitions could not be read")
            return None

        _ROLES_CACHE = roles
        return roles

    except Exception as exc:
        logger.error("Failed to fetch live CRediT roles: %s", exc)
        return None


def _fetch_single_definition(role):
    """Scrape the full entry-content (definition + examples) for a single role."""
    try:
        req = Request(role["url"], headers={"User-Agent": "CRediT-Parser/2.0"})
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")

        # Locate the entry-content div which contains the definition and task examples
        m = re.search(r'<div[^>]*class="[^"]*entry-content[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        content = m.group(1) if m else html

        # Strip <script> and <style> blocks first
        clean = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', content, flags=re.DOTALL | re.IGNORECASE)
        # Strip all HTML tags
        clean = re.sub(r'<[^>]+>', ' ', clean)
        clean = html_lib.unescape(clean)
        role["definition"] = " ".join(clean.split())
    except Exception as exc:
        logger.debug("Could not fetch definition for '%s': %s", role["name"], exc)


def _fetch_definitions_concurrently(roles):
    """Fetch all role definitions in parallel using a thread pool."""
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(_fetch_single_definition, roles))


