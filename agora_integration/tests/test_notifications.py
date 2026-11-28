from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from agora_integration.services import notifications


def _call(state: str = "Ringing") -> SimpleNamespace:
	return SimpleNamespace(
		name="CALL-1",
		state=state,
		version=1,
		project="sync_dating",
		deadline=None,
		caller="caller@sync.test",
		recipient="recipient@sync.test",
		mode="audio",
	)


class NotifyPushGateTests(TestCase):
	def _notify(self, state: str = "Ringing"):
		with (
			patch.object(notifications.frappe, "publish_realtime") as publish,
			patch.object(notifications, "enqueue_ring_push") as ring,
			patch.object(notifications, "enqueue_call_dismiss") as dismiss,
		):
			notifications.notify(_call(state))
		return publish, ring, dismiss

	def test_ringing_call_always_pushes(self) -> None:
		publish, ring, _ = self._notify()
		ring.assert_called_once()
		self.assertEqual(publish.call_count, 2)

	def test_non_ringing_call_never_rings(self) -> None:
		_, ring, _ = self._notify(state="Active")
		ring.assert_not_called()

	def test_terminal_state_dismisses_the_ring(self) -> None:
		_, ring, dismiss = self._notify("Ended")
		dismiss.assert_called_once()
		ring.assert_not_called()
