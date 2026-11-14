"""
handles the nxm:// side of Nexus Mods: parsing the URL that Windows
hands us when someone clicks "Mod Manager Download" on a Nexus mod
page, and resolving it into a real, authorized download URL.

the nxm:// format itself is confirmed against multiple independent
real implementations (a published Rust CLI's source, and a bash script
actively used by Linux modders for exactly this handoff), not just
inferred:

    nxm://<game_domain>/mods/<mod_id>/files/<file_id>?key=<key>&expires=<expires>&user_id=<user_id>

the download_link API call is reconstructed with high confidence from
multiple client libraries' documentation (pynxm, node-nexus-api,
nexus-mods-mcp) which all agree on the same shape: non-premium
accounts pass the key/expires/user_id that came from the nxm link
itself, alongside the user's own personal API key in the request
header. that said, this has NOT been exercised against the real
Nexus API - there's no way to do that without an actual Nexus account
and a live nxm click, neither of which exist in this sandbox. same
situation as steam_locator.py's registry read: built as correctly as
the evidence supports, but needs real-world verification on an actual
machine before being fully trusted.
"""

import json
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

NEXUS_API_BASE = "https://api.nexusmods.com/v1"

# Nexus throttles on a 300 request quota (600 for premium) that refills one per
# second, and is explicit that working around it can get an API key revoked.
# validate.json is exempt, which is why it is the cheap way to check a key.
VALIDATE_URL = f"{NEXUS_API_BASE}/users/validate.json"


# Nexus asks that clients identify themselves. an unlabelled client is
# indistinguishable from a scraper from their side.
USER_AGENT = "ArmyOfOne"


def default_fetch_with_headers(url: str, headers: dict) -> bytes:
    request = Request(url, headers=headers)
    with urlopen(request, timeout=10) as response:
        return response.read()


@dataclass
class NxmRequest:
    game_domain: str
    mod_id: str
    file_id: str
    key: str | None
    expires: str | None
    user_id: str | None


def parse_nxm_url(url: str) -> NxmRequest:
    """
    parses a real nxm:// url into its parts. raises ValueError on
    anything that doesn't match the expected shape, rather than
    silently returning something half-populated: a malformed nxm url
    should be a loud failure, not a mysterious download error three
    steps later.
    """
    if not url.startswith("nxm://"):
        raise ValueError(f"not an nxm:// url: {url!r}")

    parsed = urlparse(url)
    game_domain = parsed.netloc
    parts = [p for p in parsed.path.split("/") if p]

    if len(parts) < 4 or parts[0] != "mods" or parts[2] != "files":
        raise ValueError(f"nxm url doesn't match the expected /mods/<id>/files/<id> shape: {url!r}")

    mod_id = parts[1]
    file_id = parts[3]

    query = parse_qs(parsed.query)
    key = query.get("key", [None])[0]
    expires = query.get("expires", [None])[0]
    user_id = query.get("user_id", [None])[0]

    if not game_domain or not mod_id or not file_id:
        raise ValueError(f"nxm url is missing a required part: {url!r}")
    # these arrive through a protocol handler anything on the machine can
    # invoke, and they go straight into an API path carrying the user's key
    if not re.fullmatch(r"[A-Za-z0-9]+", game_domain) or not mod_id.isdigit() \
            or not file_id.isdigit():
        raise ValueError(f"nxm url has a part that is not an id: {url!r}")

    return NxmRequest(
        game_domain=game_domain, mod_id=mod_id, file_id=file_id,
        key=key, expires=expires, user_id=user_id,
    )


def get_download_url(
    api_key: str,
    nxm: NxmRequest,
    fetch: Callable[[str, dict], bytes] | None = None,
) -> str | None:
    """
    resolves an NxmRequest into a real, authorized download URL by
    calling Nexus's download_link endpoint. api_key is the user's own
    personal Nexus API key (generated once in their account settings,
    stored locally by nxm_handler.py). This identifies the app/user
    making the call. nxm.key/expires/user_id is what actually
    authorizes downloading THIS specific file without Premium.

    returns None (rather than raising) on anything that goes wrong -
    network error, malformed response, no links in the response, since
    a failed resolve should be reported as "couldn't download," not
    crash the whole flow.
    """
    url = (
        f"{NEXUS_API_BASE}/games/{nxm.game_domain}/mods/{nxm.mod_id}"
        f"/files/{nxm.file_id}/download_link.json"
    )
    params = {name: value for name, value in
              (("key", nxm.key), ("expires", nxm.expires), ("user_id", nxm.user_id)) if value}
    if params:
        url += "?" + urlencode(params)

    fetch_fn = fetch or default_fetch_with_headers

    try:
        # the other two calls in this module send USER_AGENT and this one did
        # not. an unlabelled client is what Cloudflare in front of Nexus is
        # there to stop, and the bare except below makes that 403 look
        # identical to the network being down.
        raw = fetch_fn(url, {"apikey": api_key, "User-Agent": USER_AGENT})
        links = json.loads(raw)
    except Exception:
        return None

    if not isinstance(links, list) or not links or not isinstance(links[0], dict):
        return None

    # the response is a list of mirrors, first one is fine, premium
    # users may get to pick a preferred mirror, not something worth
    # building UI around for this
    return links[0].get("URI")


@dataclass
class Account:
    """who the stored api key belongs to, and what it is allowed to do"""
    valid: bool
    name: str | None = None
    is_premium: bool = False
    error: str | None = None

    def as_dict(self) -> dict:
        return {"valid": self.valid, "name": self.name,
                "isPremium": self.is_premium, "error": self.error}


def validate_api_key(api_key: str, fetch: Callable[[str, dict], bytes] | None = None) -> Account:
    """
    checks a key and reports whether the account is Premium.

    premium is the whole reason this exists. a premium account can resolve a
    download link from the api alone, which means a patch run needs no clicks
    at all. a free account cannot, no matter how the request is shaped, so the
    UI has to know up front which experience it can offer instead of finding
    out via a 403 halfway through a run.

    this endpoint is exempt from Nexus's hourly rate limit, so calling it on
    launch costs nothing against the quota.
    """
    fetch_fn = fetch or default_fetch_with_headers
    try:
        raw = fetch_fn(VALIDATE_URL, {"apikey": api_key, "User-Agent": USER_AGENT})
        data = json.loads(raw)
    except Exception as e:
        return Account(valid=False, error=str(e))

    if not isinstance(data, dict) or "user_id" not in data:
        return Account(valid=False, error="Nexus didn't recognise that key")

    # the json uses "is_premium?" with a trailing question mark, which is a
    # ruby-ism that survived into the wire format. some responses also carry
    # the plain name, so check both rather than picking one and hoping.
    premium = bool(data.get("is_premium?") or data.get("is_premium"))
    return Account(valid=True, name=data.get("name"), is_premium=premium)


def needs_website_click(nxm: NxmRequest | None, is_premium: bool) -> bool:
    """
    whether this download still needs the user to press Mod Manager Download
    on the Nexus site.

    premium never does. free always does, because key/expires only exist on an
    nxm link the website generates. there is no third answer here and no way
    to synthesise those values, which is the point of the design.
    """
    if is_premium:
        return False
    return nxm is None or not (nxm.key and nxm.expires)


def mod_page_url(game_domain: str, mod_id: str) -> str:
    """
    lands on the FILES tab, not the description.

    without ?tab=files the user arrives at a wall of screenshots and changelog
    and has to find the tab themselves, which for a first-time modder is the
    step they get stuck on. the button they need is one scroll down from here.
    """
    return f"https://www.nexusmods.com/{game_domain}/mods/{mod_id}?tab=files"


def get_download_url_for_mod(
    api_key: str,
    game_domain: str,
    mod_id: str,
    file_id: str,
    fetch: Callable[[str, dict], bytes] | None = None,
) -> str | None:
    """premium-only path: resolve a link with no nxm round trip at all"""
    return get_download_url(
        api_key,
        NxmRequest(game_domain=game_domain, mod_id=mod_id, file_id=file_id,
                   key=None, expires=None, user_id=None),
        fetch=fetch,
    )


def latest_file_id(
    api_key: str,
    game_domain: str,
    mod_id: str,
    fetch: Callable[[str, dict], bytes] | None = None,
) -> str | None:
    """
    the newest MAIN file for a mod. mods carry optional files, patches and
    old versions in the same list, and grabbing the wrong one installs a
    hotfix without the thing it patches.
    """
    fetch_fn = fetch or default_fetch_with_headers
    url = f"{NEXUS_API_BASE}/games/{game_domain}/mods/{mod_id}/files.json?category=main"
    try:
        payload = json.loads(fetch_fn(url, {"apikey": api_key, "User-Agent": USER_AGENT}))
    except Exception:
        return None

    files = payload.get("files") if isinstance(payload, dict) else None
    if not files:
        return None

    main_files = [f for f in files if f.get("category_name") in ("MAIN", None)] or files
    newest = max(main_files, key=lambda f: f.get("uploaded_timestamp") or 0)
    file_id = newest.get("file_id")
    return str(file_id) if file_id is not None else None


def file_sizes(
    api_key: str,
    game_domain: str,
    mod_id: str,
    fetch: Callable[[str, dict], bytes] | None = None,
) -> dict:
    """
    file name -> bytes for every file on a mod's page, which is what lets the
    Downloads watch say how far along one is. {} for anything that goes
    wrong, since a percentage is never worth failing a download over
    """
    fetch_fn = fetch or default_fetch_with_headers
    url = f"{NEXUS_API_BASE}/games/{game_domain}/mods/{mod_id}/files.json"
    try:
        payload = json.loads(fetch_fn(url, {"apikey": api_key, "User-Agent": USER_AGENT}))
    except Exception:
        return {}
    out = {}
    for entry in (payload.get("files") or []) if isinstance(payload, dict) else []:
        name = entry.get("file_name") if isinstance(entry, dict) else None
        size = entry.get("size_in_bytes") if isinstance(entry, dict) else None
        if size is None and isinstance(entry, dict) and isinstance(entry.get("size_kb"), int):
            # older answers only had kilobytes, which is near enough for a percentage
            size = entry["size_kb"] * 1024
        if isinstance(name, str) and isinstance(size, int) and size > 0:
            out[name] = size
    return out
