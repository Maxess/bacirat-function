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


def _room_max_participants() -> int:
    try:
        return int(os.environ.get("LIVECLASS_ROOM_MAX_PARTICIPANTS", "200"))
    except ValueError:
        return 200


async def create_room(room_name: str) -> None:
    if _mock():
        _MOCK_ROOMS.setdefault(room_name, [])
        return None
    async def go(room):
        # max_participants جلوی اتاق‌های بی‌نهایت را می‌گیرد — همان نقش max_user_count_in_room در PersianMeet
        return await room.create_room(lk.CreateRoomRequest(
            name=room_name, max_participants=_room_max_participants()))
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


async def _with_egress(fn):
    lkapi = lk.LiveKitAPI(http_url(), _key(), _secret())
    try:
        return await fn(lkapi.egress)
    finally:
        await lkapi.aclose()


async def start_room_composite_egress(room_name: str, filepath: str) -> str:
    """ضبط کامل اتاق روی فایل MP4 — نیاز به سرویس livekit-egress دارد (LIVECLASS_EGRESS_ENABLE=1)."""
    if _mock():
        import secrets as _secrets
        return "EG_mock_" + _secrets.token_hex(6)
    async def go(egress):
        req = lk.RoomCompositeEgressRequest(
            room_name=room_name,
            layout="grid",
            file_outputs=[lk.EncodedFileOutput(file_type=lk.EncodedFileType.MP4, filepath=filepath)],
        )
        return await egress.start_room_composite_egress(req)
    resp = await _with_egress(go)
    return resp.egress_id


async def stop_egress(egress_id: str) -> None:
    if _mock() or (egress_id or "").startswith("EG_mock_"):
        return None
    async def go(egress):
        try:
            return await egress.stop_egress(lk.StopEgressRequest(egress_id=egress_id))
        except Exception:
            return None
    await _with_egress(go)
