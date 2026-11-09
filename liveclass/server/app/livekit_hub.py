# -*- coding: utf-8 -*-
"""اتصال به LiveKit — توکن‌سازی (همگام) و سرویس اتاق (async برای FastAPI)."""
from __future__ import annotations

import os
from datetime import timedelta

from livekit import api as lk

# حالت mock برای تست خودکار/توسعه‌ی آفلاین — در تولید خاموش است (LIVECLASS_LIVEKIT_MOCK=1)
_MOCK_ROOMS: dict[str, list[dict]] = {}


def _mock() -> bool:
    return os.environ.get("LIVECLASS_LIVEKIT_MOCK") == "1"


def _ws_url() -> str:
    return os.environ.get("LIVECLASS_LIVEKIT_URL", "ws://127.0.0.1:7880")


def http_url() -> str:
    url = _ws_url()
    if url.startswith("ws://"):
        return "http://" + url[len("ws://"):]
    if url.startswith("wss://"):
        return "https://" + url[len("wss://"):]
    return url


def public_url() -> str:
    return (os.environ.get("LIVECLASS_LIVEKIT_PUBLIC_URL") or _ws_url()).rstrip("/")


def _key() -> str:
    return os.environ.get("LIVECLASS_LIVEKIT_KEY", "devkey")


def _secret() -> str:
    return os.environ.get("LIVECLASS_LIVEKIT_SECRET", "secret-secret-secret-secret-secret-12")


async def _with_room(fn):
    lkapi = lk.LiveKitAPI(http_url(), _key(), _secret())
    try:
        return await fn(lkapi.room)
    finally:
        await lkapi.aclose()


async def create_room(room_name: str) -> None:
    if _mock():
        _MOCK_ROOMS.setdefault(room_name, [])
        return None
    async def go(room):
        return await room.create_room(lk.CreateRoomRequest(name=room_name))
    await _with_room(go)


async def delete_room(room_name: str) -> None:
    if _mock():
        _MOCK_ROOMS.pop(room_name, None)
        return None
    async def go(room):
        try:
            return await room.delete_room(lk.DeleteRoomRequest(room=room_name))
        except Exception:
            return None  # اتاق خالی/بسته — بی‌اهمیت
    await _with_room(go)


async def list_participants(room_name: str) -> list[dict]:
    if _mock():
        return list(_MOCK_ROOMS.get(room_name, []))
    async def go(room):
        try:
            return await room.list_participants(lk.ListParticipantsRequest(room=room_name))
        except Exception:
            return None
    resp = await _with_room(go)
    if resp is None:
        return []
    out = []
    for p in resp.participants:
        out.append({
            "sid": p.sid,
            "identity": p.identity,
            "name": p.name,
            "joined_at": int(p.joined_at) if p.joined_at else 0,
        })
    return out


async def remove_participant(room_name: str, identity: str) -> None:
    if _mock():
        _MOCK_ROOMS[room_name] = [p for p in _MOCK_ROOMS.get(room_name, []) if p.get("identity") != identity]
        return None
    async def go(room):
        return await room.remove_participant(
            lk.RoomParticipantIdentity(room=room_name, identity=identity))
    try:
        await _with_room(go)
    except Exception:
        pass


def mint_token(room_name: str, identity: str, display_name: str, *, can_publish: bool,
               ttl_seconds: int = 6 * 3600) -> str:
    grant = lk.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=can_publish,
        can_subscribe=True,
        can_publish_data=True,
    )
    token = (lk.AccessToken(_key(), _secret())
             .with_identity(identity)
             .with_name(display_name)
             .with_grants(grant)
             .with_ttl(timedelta(seconds=ttl_seconds)))
    return token.to_jwt()
