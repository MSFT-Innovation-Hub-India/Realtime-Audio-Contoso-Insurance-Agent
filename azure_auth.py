import threading
import time
from functools import lru_cache

from azure.core.credentials import AccessToken
from azure.identity import DefaultAzureCredential

VOICE_LIVE_SCOPE = "https://ai.azure.com/.default"
AZURE_SQL_SCOPE = "https://database.windows.net/.default"
AZURE_SEARCH_SCOPE = "https://search.azure.com/.default"

_token_cache: dict = {}
_token_lock = threading.Lock()


@lru_cache(maxsize=1)
def get_credential() -> DefaultAzureCredential:
    """Shared Microsoft Entra ID credential.

    Resolves to the managed identity when hosted in Azure (set AZURE_CLIENT_ID for a
    user-assigned identity) and to the developer's Azure CLI / VS Code sign-in locally.
    """
    return DefaultAzureCredential()


def get_access_token(scope: str) -> str:
    """Returns a cached access token for the scope, refreshing it 5 minutes before expiry."""
    return _get_token(scope).token


def _get_token(scope: str) -> AccessToken:
    with _token_lock:
        token = _token_cache.get(scope)
        if token is None or token.expires_on - 300 < time.time():
            token = get_credential().get_token(scope)
            _token_cache[scope] = token
        return token


class CachedTokenCredential:
    """TokenCredential backed by the shared token cache (for Azure SDK clients such as SearchClient)."""

    def get_token(self, *scopes, **kwargs) -> AccessToken:
        return _get_token(scopes[0])


def warm_up_tokens():
    """Pre-fetches tokens so the first tool call doesn't pay the credential lookup latency."""
    for scope in (VOICE_LIVE_SCOPE, AZURE_SQL_SCOPE, AZURE_SEARCH_SCOPE):
        try:
            get_access_token(scope)
        except Exception:
            pass
