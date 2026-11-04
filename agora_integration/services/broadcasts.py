"""Reusable one-publisher broadcast services; Sync owns eligibility and charging."""

import hashlib
import secrets
from typing import Any
from uuid import UUID, uuid4

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from agora_integration.services.tokens import get_settings, issue_broadcast_token

ACTIVE = ("Preparing", "Live", "Reconnecting")
RECOVERY_SECONDS = 60


def current_device() -> str:
	from flutter_utils.device_credentials import get_current_device_credential

	credential = get_current_device_credential()
	if not credential:
		frappe.throw(_("Sign in again on this device to use Live."), frappe.PermissionError)
	return credential


def _generation(user: str, *, lock: bool = True) -> int:
	doc = frappe.get_doc("User", user, for_update=lock)
	return cint(getattr(doc, "sync_registration_generation", 1)) or 1


def require_no_broadcast(user: str, except_room: str | None = None) -> None:
	"""Caller holds User lock, shared with private-call start/accept."""
	if not frappe.db.table_exists("Agora Broadcast Participant"):
		return
	rows = frappe.qb.get_query(
		"Agora Broadcast Participant",
		filters={"user": user, "active": 1, "lease_until": [">", now_datetime()]},
		fields=["broadcast"],
		for_update=True,
	).run(as_dict=True)
	# A recovering host still owns the room after its old media lease expires.
	# Filter by active room state rather than scanning all historical participants.
	hosted = frappe.qb.get_query(
		"Agora Broadcast",
		filters={"host": user, "state": ["in", ACTIVE]},
		fields=["name"],
		for_update=True,
	).run(as_dict=True)
	for room_id in sorted({row.broadcast for row in rows} | {row.name for row in hosted}):
		room = frappe.get_doc("Agora Broadcast", room_id, for_update=True)
		if room_id != except_room and visible_state(room) != "Ended":
			frappe.throw(_("Finish your current Live session before starting another media session."))


def _no_call(user: str) -> None:
	if not frappe.db.table_exists("Agora Call Session"):
		return
	from agora_integration.services.calls import ACTIVE as CALL_ACTIVE

	if frappe.qb.get_query(
		"Agora Call Session",
		filters={"state": ["in", CALL_ACTIVE]},
		or_filters={"caller": user, "recipient": user},
		fields=["name"],
		for_update=True,
	).run(as_dict=True):
		frappe.throw(_("Finish your current call before entering Live."))


def _participant(room: Any, user: str, *, create: bool = False, require_active: bool = True) -> Any:
	key = hashlib.sha256(f"{room.name}:{user}".encode()).hexdigest()
	name = frappe.db.get_value(
		"Agora Broadcast Participant", {"participant_key": key}, "name", for_update=True
	)
	if name:
		doc = frappe.get_doc("Agora Broadcast Participant", name, for_update=True)
		if (
			(doc.device != current_device() or doc.registration_generation != _generation(user))
			and doc.lease_until
			and get_datetime(doc.lease_until) > now_datetime()
		):
			frappe.throw(_("This Live session is open on another device."), frappe.PermissionError)
		if not create and (
			(require_active and not doc.active)
			or doc.device != current_device()
			or doc.registration_generation != _generation(user)
		):
			frappe.throw(_("Enter the broadcast again to obtain access."), frappe.PermissionError)
		if create:
			doc.device = current_device()
			doc.registration_generation = _generation(user)
			doc.active = 1
		return doc
	if not create:
		frappe.throw(_("Join this broadcast before requesting media access."), frappe.PermissionError)
	uid = secrets.randbelow(2**31 - 1) + 1
	while frappe.db.get_value(
		"Agora Broadcast Participant", {"rtc_key": f"{room.name}:{uid}"}, "name", for_update=True
	):
		uid = secrets.randbelow(2**31 - 1) + 1
	return frappe.get_doc(
		{
			"doctype": "Agora Broadcast Participant",
			"broadcast": room.name,
			"user": user,
			"device": current_device(),
			"uid": uid,
			"rtc_key": f"{room.name}:{uid}",
			"participant_key": key,
			"registration_generation": _generation(user),
			"active": 1,
		}
	)


def load(
	room_id: str, project: str, user: str, *, lock: bool = False, allow_disabled_host: bool = False
) -> Any:
	room = frappe.get_doc("Agora Broadcast", room_id)
	if room.project != project:
		frappe.throw(_("Broadcast not found."), frappe.PermissionError)
	if lock:
		for participant_user in sorted({user, room.host}):
			if not frappe.db.get_value("User", participant_user, "enabled", for_update=True) and not (
				allow_disabled_host and participant_user != user
			):
				frappe.throw(_("This account is unavailable."), frappe.PermissionError)
		room = frappe.get_doc("Agora Broadcast", room_id, for_update=True)
		expire(room)
	return room


def visible_state(room: Any) -> str:
	if room.state == "Ended" or get_datetime(room.deadline) > now_datetime():
		return room.state
	if room.state == "Live" and add_to_date(room.deadline, seconds=RECOVERY_SECONDS) > now_datetime():
		return "Reconnecting"
	return "Ended"


def _transition(room: Any, **values: Any) -> None:
	# Recovery may be observed by a silent viewer. Never disclose that observer
	# through room editor metadata or list_update actor payloads.
	values["version"] = cint(room.version) + 1
	room.update(values)
	frappe.db.set_value("Agora Broadcast", room.name, values, update_modified=False)
	room.notify_update()


def expire(room: Any) -> None:
	"""Caller holds the room lock. Missed heartbeats cannot mint a fresh window."""
	state = visible_state(room)
	if state == room.state:
		return
	if state == "Reconnecting":
		_transition(room, state=state, deadline=add_to_date(room.deadline, seconds=RECOVERY_SECONDS))
	else:
		_transition(room, state="Ended", ended_at=now_datetime())


def interrupt(room: Any, user: str) -> None:
	if user != room.host:
		frappe.throw(_("Only the host can interrupt this broadcast."), frappe.PermissionError)
	_participant(room, user)
	expire(room)
	if room.state == "Live":
		_transition(
			room, state="Reconnecting", deadline=add_to_date(now_datetime(), seconds=RECOVERY_SECONDS)
		)


def serialize(room: Any, user: str) -> dict[str, Any]:
	state = visible_state(room)
	return {
		"id": room.name,
		"host": room.host,
		"host_name": frappe.db.get_value("User", room.host, "first_name") or "Host",
		"host_uid": cint(
			frappe.db.get_value(
				"Agora Broadcast Participant", {"broadcast": room.name, "user": room.host}, "uid"
			)
		),
		"title": room.title,
		"state": state,
		"accepting_admissions": state == "Live",
		"recovery_deadline": str(
			add_to_date(room.deadline, seconds=RECOVERY_SECONDS) if room.state == "Live" else room.deadline
		)
		if state == "Reconnecting"
		else "",
		"is_host": user == room.host,
		"version": cint(room.version),
		"viewer_count": viewer_counts([room.name]).get(room.name, 0)
		if state in ("Live", "Reconnecting")
		else 0,
		"started_at": str(room.started_at or ""),
		"ended_at": str(room.ended_at or ""),
		"joining_fee": "0",
	}


def viewer_counts(room_ids: list[str]) -> dict[str, int]:
	"""Aggregate current audience leases without exposing participant identities."""
	if not room_ids:
		return {}
	from frappe.query_builder.functions import Count

	p = frappe.qb.DocType("Agora Broadcast Participant")
	r = frappe.qb.DocType("Agora Broadcast")
	u = frappe.qb.DocType("User")
	from frappe.query_builder.functions import Coalesce, NullIf

	generation = (
		Coalesce(NullIf(u.sync_registration_generation, 0), 1)
		if frappe.db.has_column("User", "sync_registration_generation")
		else 1
	)
	rows = (
		frappe.qb.from_(p)
		.join(r)
		.on(p.broadcast == r.name)
		.join(u)
		.on(p.user == u.name)
		.select(p.broadcast, Count(p.name).as_("viewers"))
		.where(
			p.broadcast.isin(room_ids)
			& (p.active == 1)
			& (p.user != r.host)
			& (p.lease_until > now_datetime())
			& (u.enabled == 1)
			& (p.registration_generation == generation)
		)
		.groupby(p.broadcast)
	).run(as_dict=True)
	return {row.broadcast: cint(row.viewers) for row in rows}


def prepare(project: str, user: str, title: str, request_id: str) -> Any:
	get_settings()
	if not isinstance(title, str) or not 1 <= len(title.strip()) <= 60:
		frappe.throw(_("Enter a Live title of 1 to 60 characters."))
	try:
		request_id = str(UUID(request_id))
	except TypeError, ValueError, AttributeError:
		frappe.throw(_("A valid request UUID is required."))
	frappe.db.get_value("User", user, "enabled", for_update=True)
	key = hashlib.sha256(f"{project}:{user}:{_generation(user)}:{request_id}".encode()).hexdigest()
	existing = frappe.db.get_value("Agora Broadcast", {"request_key": key}, "name", for_update=True)
	if existing:
		room = frappe.get_doc("Agora Broadcast", existing, for_update=True)
		if room.title != title.strip():
			frappe.throw(_("This request belongs to another broadcast title."))
		return room
	_no_call(user)
	require_no_broadcast(user)
	room = frappe.get_doc(
		{
			"doctype": "Agora Broadcast",
			"project": project,
			"host": user,
			"host_generation": _generation(user),
			"request_key": key,
			"channel": f"live_{uuid4().hex}",
			"title": title.strip(),
			"state": "Preparing",
			"version": 1,
			"deadline": add_to_date(now_datetime(), seconds=60),
		}
	)
	room.insert(ignore_permissions=True)
	participant = _participant(room, user, create=True)
	participant.lease_until = room.deadline
	participant.last_seen = now_datetime()
	participant.insert(ignore_permissions=True)
	return room


def join(room: Any, user: str) -> None:
	if visible_state(room) != "Live":
		frappe.throw(_("This broadcast is not accepting viewers."))
	_no_call(user)
	require_no_broadcast(user, room.name)
	participant = _participant(room, user, create=True)
	participant.last_seen = now_datetime()
	participant.lease_until = add_to_date(now_datetime(), seconds=90)
	participant.save(ignore_permissions=True)
	_membership_changed(room)


def _membership_changed(room: Any) -> None:
	# This is an aggregate invalidation, not a public content edit by the viewer.
	# Preserve the host's content-editor metadata; actor details stay private on
	# the participant row. Publish only after the containing transaction commits.
	room.version = cint(room.version) + 1
	frappe.db.set_value("Agora Broadcast", room.name, "version", room.version, update_modified=False)
	room.notify_update()


def credentials(room: Any, user: str) -> dict[str, Any]:
	expire(room)
	if visible_state(room) == "Ended":
		frappe.throw(_("This broadcast has ended."))
	participant = _participant(room, user)
	_no_call(user)
	require_no_broadcast(user, room.name)
	if room.state == "Preparing" and room.host != user:
		frappe.throw(_("The host is not ready yet."))
	participant.last_seen = now_datetime()
	participant.lease_until = add_to_date(now_datetime(), seconds=90)
	participant.save(ignore_permissions=True)
	if user == room.host and room.state != "Reconnecting":
		room.deadline = add_to_date(now_datetime(), seconds=90)
		# Lease refresh is internal; do not fan out a room reload every heartbeat.
		frappe.db.set_value("Agora Broadcast", room.name, "deadline", room.deadline, update_modified=False)
	ttl = (
		min(90, max(1, int((get_datetime(room.deadline) - now_datetime()).total_seconds())))
		if room.state == "Reconnecting"
		else 90
	)
	return issue_broadcast_token(room.channel, cint(participant.uid), publisher=user == room.host, ttl=ttl)


def activate(room: Any, user: str) -> None:
	if user != room.host:
		frappe.throw(_("Only the host can start the broadcast."), frappe.PermissionError)
	_participant(room, user)
	expire(room)
	if visible_state(room) == "Ended":
		frappe.throw(_("This broadcast has ended."))
	if room.state == "Live":
		return
	room.state, room.started_at = "Live", room.started_at or now_datetime()
	room.deadline = add_to_date(now_datetime(), seconds=60)
	room.version = cint(room.version) + 1
	room.save(ignore_permissions=True)


def leave(room: Any, user: str, *, end: bool = False) -> None:
	participant = _participant(room, user, require_active=False)
	if end and room.host != user:
		frappe.throw(_("Only the host can end the broadcast."), frappe.PermissionError)
	if room.host == user:
		if room.state != "Ended":
			room.state, room.ended_at = "Ended", now_datetime()
			room.version = cint(room.version) + 1
			room.save(ignore_permissions=True)
	else:
		# The existing bearer token remains a bounded lease; do not claim instant revocation.
		participant.active = 0
		participant.save(ignore_permissions=True)
		_membership_changed(room)


def has_permission(doc: Any, ptype: str | None = None, user: str | None = None) -> bool:
	"""Read-only room subscriptions; media credentials never live in this document."""
	user = user or frappe.session.user
	if (
		ptype not in (None, "read", "select")
		or user == "Guest"
		or not frappe.db.get_value("User", user, "enabled")
	):
		return False
	if "System Manager" in frappe.get_roles(user):
		return True
	if user == doc.host:
		return doc.host_generation == _generation(user, lock=False)
	return bool(
		frappe.db.exists(
			"Agora Broadcast Participant",
			{
				"broadcast": doc.name,
				"user": user,
				"active": 1,
				"registration_generation": _generation(user, lock=False),
			},
		)
	)


def query_conditions(user: str | None = None) -> str:
	# Discovery is a policy-filtered project API, not an unrestricted resource list.
	user = user or frappe.session.user
	return "" if user != "Guest" and "System Manager" in frappe.get_roles(user) else "1=0"
