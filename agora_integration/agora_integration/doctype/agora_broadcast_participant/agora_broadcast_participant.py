from frappe.model.document import Document


class AgoraBroadcastParticipant(Document):
	def notify_update(self) -> None:
		# Private presence rows do not publish actor-bearing list events. Visible
		# membership changes invalidate the aggregate room through its safe event.
		return
