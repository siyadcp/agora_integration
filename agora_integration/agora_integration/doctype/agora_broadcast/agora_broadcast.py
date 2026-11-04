import frappe
from frappe.model.document import Document


class AgoraBroadcast(Document):
	def notify_update(self) -> None:
		# Broadcast metadata is readable by participants. Standard list_update's
		# actor would identify silent viewers when membership changes.
		if any(
			getattr(frappe.flags, key, False) for key in ("in_import", "in_patch", "in_migrate", "in_install")
		):
			return
		frappe.publish_realtime(
			"doc_update",
			{"modified": self.modified, "doctype": self.doctype, "name": self.name},
			doctype=self.doctype,
			docname=self.name,
			after_commit=True,
		)
		frappe.publish_realtime(
			# Website users have no unrestricted doctype/list permission. This
			# content-free hint reaches their existing authenticated socket room;
			# policy-filtered discovery must refetch before displaying anything.
			"list_update",
			{"doctype": self.doctype, "name": "*"},
			room="website",
			after_commit=True,
		)
