"""Bounded, inert JSON call context. Identity and permissions never come from it."""

import json
from typing import Any


def normalize_metadata(value: Any = None) -> dict[str, Any]:
	if value is None or value == "":
		return {}
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (ValueError, RecursionError) as error:
			raise ValueError("Invalid call metadata.") from error
	if not isinstance(value, dict):
		raise ValueError("Call metadata must be an object.")
	try:
		encoded = json.dumps(value, allow_nan=False, sort_keys=True, ensure_ascii=True)
	except (ValueError, TypeError, RecursionError) as error:
		raise ValueError("Invalid call metadata.") from error
	if len(encoded.encode()) > 4096:
		raise ValueError("Call metadata must be at most 4 KB.")
	for key in ("title", "subtitle", "context_type", "context_id"):
		if key in value and (not isinstance(value[key], str) or len(value[key]) > 200):
			raise ValueError("Invalid call display text.")
	fields = value.get("display_fields", [])
	if not isinstance(fields, list) or len(fields) > 8:
		raise ValueError("Invalid call display fields.")
	for field in fields:
		if not isinstance(field, dict) or any(
			not isinstance(field.get(key), str) or len(field[key]) > 200 for key in ("label", "value")
		):
			raise ValueError("Invalid call display field.")
	return json.loads(encoded)
