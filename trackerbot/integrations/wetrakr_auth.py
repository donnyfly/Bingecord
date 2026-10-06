"""Per-user OAuth token refresh with rotation-safe persistence."""

import asyncio
from datetime import datetime, timedelta, timezone


class WeTrakrAuth:
    def __init__(self, client, store, provider="wetrakr"):
        self.provider = provider
        self.client = client
        self.store = store
        self._locks = {}

    async def access_token(self, discord_user_id: str) -> str:
        uid = str(discord_user_id)
        lock = self._locks.setdefault(uid, asyncio.Lock())
        async with lock:
            user = await self.store.get_user(uid)
            link = (user or {}).get(self.provider)
            if not link:
                raise ValueError(f"{self.provider} is not linked")
            expires = link.get("expires_at")
            if expires:
                expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if expiry > datetime.now(timezone.utc) + timedelta(hours=1):
                    return link["access_token"]
            # A missing expiry is treated as expired. Never replay a rotated
            # refresh token on another invocation after storage succeeds.
            refreshed = await self.client.refresh_token(link["refresh_token"])
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(refreshed.get("expires_in", 604800)))
            saved = await self.store.rotate_wetrakr_tokens(
                uid, link["account_id"], link["refresh_token"],
                {"access_token": refreshed["access_token"],
                 "refresh_token": refreshed.get("refresh_token") or link["refresh_token"],
                 "expires_at": expires_at.isoformat()}, provider=self.provider,
            )
            if saved:
                return refreshed["access_token"]
            # A relink/unlink may have raced this request. Do not return a
            # token for an account that the user has since removed.
            latest = await self.store.get_user(uid)
            current = (latest or {}).get(self.provider)
            if current and str(current.get("account_id")) == str(link["account_id"]):
                return current["access_token"]
            raise ValueError(f"{self.provider} link changed during token refresh")
