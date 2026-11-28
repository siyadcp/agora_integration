from typing import Any

import frappe

from agora_integration.services.push import (
	DISMISS_STATES,
	enqueue_call_dismiss,
	enqueue_ring_push,
)


def notify(call: Any) -> None:
	payload = {
		"call_id": call.name,
		"state": call.state,
		"version": call.version,
		"project": call.project,
		"expires_at": str(call.deadline or ""),
	}
	for user in (call.caller, call.recipient):
		frappe.publish_realtime("agora_call_updated", payload, user=user, after_commit=True)
	# The data push mirrors the realtime event. Presence is only advisory: a
	# killed app or a dead socket can leave the last report as Foreground, and
	# suppressing the push then loses the call entirely. Foreground clients
	# ignore the native ring and simply refresh from the data event.
	if call.state == "Ringing":
		enqueue_ring_push(call)
	elif call.state in DISMISS_STATES:
		enqueue_call_dismiss(call)
