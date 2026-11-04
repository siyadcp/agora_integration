"""Decode issued RTC privileges at the public token boundary."""

import base64
import struct
import unittest
import zlib

from agora_integration.services.tokens import build_broadcast_token, build_rtc_token


def privileges(token: str) -> dict[int, int]:
	data = zlib.decompress(base64.b64decode(token[3:]))
	offset = 2 + struct.unpack_from("<H", data, 0)[0]
	offset += 2 + struct.unpack_from("<H", data, offset)[0]
	offset += 14
	service, count = struct.unpack_from("<HH", data, offset)
	assert service == 1
	return dict(struct.unpack_from("<HI", data, offset + 4 + index * 6) for index in range(count))


class BroadcastTokenTests(unittest.TestCase):
	def test_audience_only_receives_a_bound_short_join_privilege(self) -> None:
		token = build_broadcast_token("a" * 32, "b" * 32, "live_test", 17, 30, publisher=False)
		self.assertEqual(privileges(token), {1: 30})
		self.assertEqual(
			privileges(build_rtc_token("a" * 32, "b" * 32, "call_test", 17, 600)),
			{1: 600, 2: 600, 3: 600, 4: 600},
		)

	def test_invalid_identifiers_roles_and_noninteger_leases_are_rejected(self) -> None:
		for uid, ttl, publisher in [
			(1.5, 30, False),
			(17, 2.5, False),
			(17, 30, "audience"),
			(0, 30, False),
			(17, 0, False),
		]:
			with self.subTest(uid=uid, ttl=ttl, publisher=publisher):
				with self.assertRaises(ValueError):
					build_broadcast_token("a" * 32, "b" * 32, "live_test", uid, ttl, publisher=publisher)
