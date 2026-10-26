import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import frappe

from agora_integration.services import calls
from agora_integration.services.context import normalize_metadata


class CallContextTests(unittest.TestCase):
	def test_old_and_json_contexts(self) -> None:
		self.assertEqual(normalize_metadata(None), {})
		self.assertEqual(
			normalize_metadata('{"title":"Help","extra":{"value":7}}'),
			{
				"title": "Help",
				"extra": {"value": 7},
			},
		)

	def test_invalid_contexts(self) -> None:
		for value in (
			[],
			"bad json",
			{"title": 3},
			{"title": "x" * 201},
			{"extra": "x" * 4097},
			{"display_fields": [{}]},
			{"x": float("nan")},
		):
			with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
				normalize_metadata(value)

	def test_retry_cannot_change_purpose_or_context(self) -> None:
		call = SimpleNamespace(
			recipient="recipient", mode="audio", purpose="support", metadata='{"title":"Help"}'
		)
		with (
			patch.object(calls, "_user", return_value="caller"),
			patch.object(calls, "get_settings"),
			patch.object(calls, "_load", return_value=call),
			patch.object(calls, "serialize", return_value={"id": "same"}),
			patch.object(calls, "_", side_effect=lambda text: text),
			patch.object(frappe, "db", Mock(get_value=Mock(side_effect=[1, 1, "existing"] * 3))),
			patch.object(frappe, "throw", side_effect=ValueError),
		):
			request_id = str(uuid4())
			self.assertEqual(
				calls.start_call(
					"project",
					"recipient",
					"audio",
					request_id,
					Mock(),
					purpose="support",
					metadata={"title": "Help"},
				),
				{"id": "same"},
			)
			with self.assertRaises(ValueError):
				calls.start_call(
					"project",
					"recipient",
					"audio",
					request_id,
					Mock(),
					purpose="briefing",
					metadata={"title": "Help"},
				)
			with self.assertRaises(ValueError):
				calls.start_call(
					"project",
					"recipient",
					"audio",
					request_id,
					Mock(),
					purpose="support",
					metadata={"title": "Changed"},
				)
