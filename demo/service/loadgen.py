"""Steady synthetic traffic against the frontend (browse + checkout)."""

import asyncio
import contextlib
import os

import httpx

TARGET = os.environ.get("TARGET", "http://frontend:8000")
RPS = float(os.environ.get("RPS", "8"))


async def main() -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        i = 0
        while True:
            route = "checkout" if i % 2 else "browse"
            asyncio.create_task(hit(client, f"{TARGET}/r/{route}"))
            i += 1
            await asyncio.sleep(1 / RPS)


async def hit(client: httpx.AsyncClient, url: str) -> None:
    with contextlib.suppress(httpx.HTTPError):
        await client.get(url)


if __name__ == "__main__":
    asyncio.run(main())
