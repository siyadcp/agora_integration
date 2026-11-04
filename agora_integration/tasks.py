import frappe

from agora_integration.services.calls import ACTIVE, expire


def expire_calls() -> None:
	"""Bounded recovery pass; each session is independent and locked before expiry."""
	names = frappe.get_all(
		"Agora Call Session",
		filters={"state": ["in", ACTIVE]},
		pluck="name",
		order_by="modified asc",
		limit=500,
	)
	for name in names:
		call = frappe.get_doc("Agora Call Session", name, for_update=True)
		expire(call)
		frappe.db.commit()


def expire_broadcast_access() -> None:
	"""Recovery need not run within 30s: provisional join privilege itself expires.

	The minute sweep persists outcomes/removal intents even without another client
	request. Provider work is always after this transaction commits.
	"""
	from agora_integration.services import broadcasts
	from agora_integration.services.broadcast_removal import reconcile

	expire_broadcasts()
	for row in frappe.get_all(
		"Agora Broadcast Participant",
		filters={
			"active": 1,
			"entry_state": "Pending",
			"entry_deadline": ["<=", frappe.utils.now_datetime()],
		},
		fields=["broadcast", "user"],
		limit_page_length=500,
		order_by="entry_deadline asc",
	):
		room = frappe.get_doc("Agora Broadcast", row.broadcast, for_update=True)
		broadcasts.expire_entry(room, row.user)
		frappe.db.commit()
	reconcile()


def expire_broadcasts() -> None:
	"""Persist time-derived recovery/end even when every client has terminated."""
	from agora_integration.services import broadcasts

	names = frappe.get_all(
		"Agora Broadcast",
		filters={"state": ["in", broadcasts.ACTIVE], "deadline": ["<=", frappe.utils.now_datetime()]},
		pluck="name",
		order_by="deadline asc",
		limit=500,
	)
	for name in names:
		room = frappe.get_doc("Agora Broadcast", name, for_update=True)
		broadcasts.expire(room)
		for user in frappe.get_all(
			"Agora Broadcast Participant", filters={"broadcast": name, "active": 1}, pluck="user"
		):
			broadcasts.expire_entry(room, user)
		frappe.db.commit()
