"""RTC-only AccessToken2 encoding, following AgoraIO/Tools' Token007 format.

Reference: DynamicKey/AgoraDynamicKey/python3/src/AccessToken2.py and
RtcTokenBuilder2.py. Uses only Python's standard library. No secrets are cached.
"""

import base64
import hashlib
import hmac
import re
import secrets
import struct
import time
import zlib
from typing import Any

import frappe
from frappe import _


def _string(value: bytes) -> bytes:
	return struct.pack("<H", len(value)) + value


def build_rtc_token(
	app_id: str,
	certificate: str,
	channel: str,
	uid: int,
	ttl: int,
	issue_time: int | None = None,
	salt: int | None = None,
) -> str:
	"""Encode join and publisher privileges for a nonzero, channel-bound UID."""
	if not all(re.fullmatch(r"[a-fA-F0-9]{32}", value) for value in (app_id, certificate)):
		raise ValueError("Invalid Agora identifiers")
	if not 0 < uid < 2**32 or not 60 <= ttl <= 3600 or not re.fullmatch(r"[a-zA-Z0-9_-]{1,63}", channel):
		raise ValueError("Invalid RTC token parameters")
	return _encode(app_id, certificate, channel, uid, ttl, (1, 2, 3, 4), issue_time, salt)


def build_broadcast_token(
	app_id: str,
	certificate: str,
	channel: str,
	uid: int,
	ttl: int,
	*,
	publisher: bool,
	issue_time: int | None = None,
) -> str:
	"""Audience has join only; separate short leases leave private-call defaults intact."""
	if not all(re.fullmatch(r"[a-fA-F0-9]{32}", value) for value in (app_id, certificate)):
		raise ValueError("Invalid Agora identifiers")
	if (
		type(uid) is not int
		or type(ttl) is not int
		or type(publisher) is not bool
		or not 0 < uid < 2**32
		or not 1 <= ttl <= 300
		or not re.fullmatch(r"[a-zA-Z0-9_-]{1,63}", channel)
	):
		raise ValueError("Invalid broadcast token parameters")
	return _encode(app_id, certificate, channel, uid, ttl, (1, 2, 3, 4) if publisher else (1,), issue_time)


def _encode(
	app_id: str,
	certificate: str,
	channel: str,
	uid: int,
	ttl: int,
	privileges: tuple[int, ...],
	issue_time: int | None = None,
	salt: int | None = None,
) -> str:
	issued = issue_time if issue_time is not None else int(time.time())
	nonce = salt if salt is not None else secrets.randbelow(99999999) + 1
	key = hmac.new(struct.pack("<I", issued), certificate.encode(), hashlib.sha256).digest()
	key = hmac.new(struct.pack("<I", nonce), key, hashlib.sha256).digest()
	# Encode the join privilege bound too, not just the token lifetime. Actual
	# already-connected client cutoff remains a provider/native launch check.
	service = struct.pack("<HH", 1, len(privileges))
	service += b"".join(struct.pack("<HI", privilege, ttl) for privilege in privileges)
	service += _string(channel.encode()) + _string(str(uid).encode())
	info = _string(app_id.encode()) + struct.pack("<IIIH", issued, ttl, nonce, 1) + service
	signature = hmac.new(key, info, hashlib.sha256).digest()
	return "007" + base64.b64encode(zlib.compress(_string(signature) + info)).decode()


def get_settings() -> Any:
	settings = frappe.get_single("Agora Settings")
	if not settings.enabled:
		frappe.throw(_("Calling is not configured or is disabled."))
	certificate = settings.get_password("app_certificate", raise_exception=False) or ""
	if not all(re.fullmatch(r"[a-fA-F0-9]{32}", value or "") for value in (settings.app_id, certificate)):
		frappe.throw(_("Ask an administrator to configure Agora credentials."))
	return settings


def issue_token(channel: str, uid: int) -> dict[str, Any]:
	settings = get_settings()
	ttl = int(settings.token_ttl or 600)
	return {
		"app_id": settings.app_id,
		"channel": channel,
		"uid": uid,
		"token": build_rtc_token(
			settings.app_id, settings.get_password("app_certificate"), channel, uid, ttl
		),
		"expires_at": int(time.time()) + ttl,
	}


def issue_broadcast_token(
	channel: str, uid: int, *, publisher: bool, ttl: int = 30, not_after: int | None = None
) -> dict[str, Any]:
	settings = get_settings()
	issued = int(time.time())
	if not_after is not None:
		ttl = min(ttl, not_after - issued)
	if ttl < 1:
		frappe.throw(_("This admission attempt has expired."))
	return {
		"app_id": settings.app_id,
		"channel": channel,
		"uid": uid,
		"token": build_broadcast_token(
			settings.app_id,
			settings.get_password("app_certificate"),
			channel,
			uid,
			ttl,
			publisher=publisher,
			issue_time=issued,
		),
		"expires_at": issued + ttl,
		"ttl": ttl,
		"publisher": publisher,
	}
