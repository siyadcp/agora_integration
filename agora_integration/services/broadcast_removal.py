"""Durable channel/UID bans, never a claim of instantaneous token revocation.

REST customer credentials are runtime-only and distinct from App Certificate.
Positive-duration retry bans are monotone: a lost response may create another
short-lived rule for the SAME retired UID/channel, never widen access. Retired
UIDs are never reused. ProviderAccepted means acknowledgement, not device proof.
"""

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import frappe
import requests
from frappe.utils import cint, get_datetime, now_datetime


def request_removal(room: Any, participant: Any, reason: str) -> None:
	if not participant.credential_expires_at or not participant.credential_app_id:
		return
	key = f"{room.name}:{participant.uid}"
	if frappe.db.exists("Agora Broadcast Removal", {"removal_key": key}):
		return
	doc = frappe.get_doc(
		{
			"doctype": "Agora Broadcast Removal",
			"removal_key": key,
			"broadcast": room.name,
			"participant": participant.name,
			"app_id": participant.credential_app_id,
			"channel": room.channel,
			"uid": participant.uid,
			"expires_at": participant.credential_expires_at,
			"reason": reason,
			"state": "Pending",
		}
	).insert(ignore_permissions=True)
	frappe.enqueue(
		"agora_integration.services.broadcast_removal.enforce", removal_id=doc.name, enqueue_after_commit=True
	)


def ban(app_id: str, channel: str, uid: int, seconds: int, *, recovering: bool = False) -> str:
	"""Only called by background work. Never log HTTP exceptions or responses."""
	if not (
		cint(frappe.conf.get("agora_broadcast_removal_enabled"))
		and frappe.conf.get("agora_rest_customer_id")
		and frappe.conf.get("agora_rest_customer_secret")
	):
		raise RuntimeError("Provider removal is not configured")
	auth = (frappe.conf.agora_rest_customer_id, frappe.conf.agora_rest_customer_secret)
	if recovering:
		# Recover a lost POST acknowledgement before creating another restriction.
		# GET may omit rules under network trouble; a repeated positive-duration
		# ban remains safe because this channel/UID is permanently retired.
		response = requests.get(
			"https://api.agora.io/dev/v1/kicking-rule",
			auth=auth,
			params={"appid": app_id},
			timeout=20,
			allow_redirects=False,
		)
		response.raise_for_status()
		data = response.json()
		if data.get("status") != "success":
			raise RuntimeError("Provider reconciliation unconfirmed")
		for rule in data.get("rules", []):
			if (
				rule.get("appid") == app_id
				and rule.get("cname") == channel
				and rule.get("uid") == uid
				and not rule.get("ip")
				and "join_channel" in rule.get("privileges", [])
				and str(rule.get("id", "")).isdigit()
			):
				expiry = datetime.fromisoformat(rule["ts"].replace("Z", "+00:00"))
				if expiry.tzinfo is None:
					expiry = expiry.replace(tzinfo=UTC)
				if expiry >= datetime.now(UTC) + timedelta(seconds=seconds - 10):
					return str(rule["id"])
	response = requests.post(
		"https://api.agora.io/dev/v1/kicking-rule",
		auth=auth,
		json={
			"appid": app_id,
			"cname": channel,
			"uid": uid,
			"time_in_seconds": max(10, seconds),
			"privileges": ["join_channel"],
		},
		timeout=20,
		allow_redirects=False,
	)
	if response.status_code != 200:
		raise RuntimeError("Provider did not acknowledge removal")
	data = response.json()
	if data.get("status") != "success" or not str(data.get("id", "")).isdigit():
		raise RuntimeError("Provider did not acknowledge a rule identity")
	return str(data["id"])


def enforce(removal_id: str) -> None:
	doc = frappe.get_doc("Agora Broadcast Removal", removal_id, for_update=True)
	if doc.state in ("ProviderAccepted", "Expired"):
		return
	remaining = math.ceil((get_datetime(doc.expires_at) - now_datetime()).total_seconds())
	if remaining <= 0:
		doc.state = "Expired"  # Credential bound elapsed; NOT observed provider removal.
	else:
		doc.attempts = cint(doc.attempts) + 1
		try:
			doc.rule_id = ban(
				doc.app_id, doc.channel, cint(doc.uid), remaining + 10, recovering=doc.attempts > 1
			)
			doc.state, doc.observed_at, doc.last_error = "ProviderAccepted", now_datetime(), ""
		except Exception:
			# Includes timeouts/lost responses: outcome unknown. Retrying only adds
			# the same bounded restriction. No secrets/response bodies/URLs in logs.
			doc.state, doc.last_error = (
				"Failed",
				"Provider removal unconfirmed; retry pending credential expiry.",
			)
	# Outside the exception handler: telemetry must not capture the HTTP exception
	# (which can hold authentication headers or raw provider response bodies).
	if doc.state == "Failed":
		frappe.log_error(
			title="Broadcast removal unconfirmed",
			message=f"Removal {doc.name}: outcome unconfirmed; reconciliation will retry until credential expiry.",
		)
	doc.save(ignore_permissions=True)


def reconcile() -> None:
	for name in frappe.get_all(
		"Agora Broadcast Removal",
		filters={"state": ["in", ["Pending", "Failed"]]},
		pluck="name",
		limit_page_length=500,
		order_by="modified asc",
	):
		frappe.enqueue(
			"agora_integration.services.broadcast_removal.enforce", removal_id=name, enqueue_after_commit=True
		)
