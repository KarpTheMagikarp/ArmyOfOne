"""
decides how a Nexus-hosted download can actually be obtained on THIS account,
and says so plainly instead of failing with a 403 halfway through a run.

there are exactly three answers and the account tier picks between them:

  premium  -> resolve a CDN link from the api and download it. no clicks, no
              browser, fully unattended. this is the "press apply and walk
              away" experience, and it needs no registered application id, so
              it works today.
  free     -> Nexus requires a key/expires pair that only the website can
              mint, handed over as an nxm:// link when the user presses Mod
              Manager Download. we open the mod page and the nxm handler
              finishes the job when they click. one click per mod.
  no key   -> nothing is possible until an api key is stored.

the free path is not an implementation gap, it is Nexus's deliberate design
to protect the ad impressions that fund the free tier. there is no request
shape that produces those values without the website, and attempting to
manufacture them is exactly the behaviour that gets api keys revoked. so this
module never tries; it reports NEEDS_WEBSITE and hands back the page url.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from patcher.mods.nexus_api import (
    Account,
    latest_file_id,
    mod_page_url,
    get_download_url_for_mod,
    validate_api_key,
)
from patcher.mods.nxm_handler import load_api_key

READY = "ready"
NEEDS_WEBSITE = "needs_website"
NEEDS_ACCOUNT = "needs_account"
NEEDS_FILE = "needs_file"
UNAVAILABLE = "unavailable"


@dataclass
class Resolution:
    status: str
    download_url: str | None = None
    page_url: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == READY


class NexusSource:
    """
    holds the api key and the account tier for one run, so a batch of Nexus
    patches costs one validate call rather than one per patch. the quota is
    300 requests an hour and Nexus is explicit that abusing it can cost the
    user their key.
    """

    def __init__(self, config_path: Path, fetch=None, account: Account | None = None):
        self._config_path = config_path
        self._fetch = fetch
        self._api_key = load_api_key(config_path)
        self._account = account
        self._checked = account is not None

    @property
    def api_key(self) -> str | None:
        return self._api_key

    def account(self) -> Account:
        # cached: validate.json is rate-limit exempt but it is still a round
        # trip, and a five patch run would otherwise make five of them
        if not self._checked:
            if self._api_key is None:
                self._account = Account(valid=False, error="no api key stored")
            else:
                self._account = validate_api_key(self._api_key, fetch=self._fetch)
            self._checked = True
        return self._account

    def is_premium(self) -> bool:
        return self.account().is_premium

    def resolve(self, game_domain: str, mod_id: str, file_id: str | None = None) -> Resolution:
        """
        how this mod can be obtained on this install.

        the DEFAULT answer is NEEDS_FILE: open the mod page, let the user
        download it in their own browser, and take the file they drop on the
        window. that path has no preconditions at all. the api route needs an
        api key, a mod with Mod Manager Download enabled, a Premium account
        and ownership of the nxm:// protocol, and every one of those is a way
        for it to fail in front of someone who just wanted the rain fixed.

        a stored Premium key is treated as an accelerator, nothing more. when
        it works the download is silent; when it does not, the drop path is
        still there and is the same path everyone else uses.
        """
        page = mod_page_url(game_domain, mod_id)

        if self._api_key is None:
            return Resolution(NEEDS_FILE, page_url=page,
                              detail="we'll open the mod page and pick up the download automatically")

        account = self.account()
        if not account.valid or not account.is_premium:
            # a free account cannot resolve a link no matter how it asks, and
            # an invalid key is not worth a second message. both land on the
            # path that always works.
            return Resolution(NEEDS_FILE, page_url=page,
                              detail="we'll open the mod page and pick up the download automatically")

        if file_id is None:
            file_id = latest_file_id(self._api_key, game_domain, mod_id, fetch=self._fetch)
            if file_id is None:
                return Resolution(NEEDS_FILE, page_url=page,
                                  detail="we'll open the mod page and pick up the download automatically")

        url = get_download_url_for_mod(self._api_key, game_domain, mod_id, file_id,
                                       fetch=self._fetch)
        if url is None:
            return Resolution(NEEDS_FILE, page_url=page,
                              detail="we'll open the mod page and pick up the download automatically")
        return Resolution(READY, download_url=url, page_url=page)
