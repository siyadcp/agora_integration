# Broadcast admission and removal

Live remains disabled by default. This implementation is not provider/native
verification or authorization to enable paid admission.

- A viewer joins with a server attempt, generation, bound managed device/user,
  broadcast and UID. The authenticated app reports its first rendered host frame.
  This is client reporting, **not provider attestation**; a modified app can lie.
  Joining alone does not confirm. The original 30-second deadline cannot renew.
- Pending credentials encode only join privilege, with both token and join
  privilege lifetime bounded by that deadline. Confirmed credentials last at
  most 90 seconds. Private-call token defaults are unchanged.
- Leave, failed entry and room termination retire credentials locally, deny
  renewal, and persist channel/UID removal intent. Re-entry uses a new UID and
  attempt. Retired UIDs are not reused while their durable records exist.
- Provider work is queued after commit. A minute recovery sweep expires pending
  entries and stale rooms and requeues pending/failed removal work. Credential
  expiry, not scheduler punctuality, supplies the provisional cutoff bound.
- Terminal cleanup enumerates participants with current reads under the room
  lock; an earlier REPEATABLE READ snapshot cannot hide a newly committed viewer.
  Reads and the bounded sweep also repair active access left on already-ended
  rooms, including confirmed viewers, without requiring another viewer callback.
- `Pending` is durable intent; `Failed` means the provider outcome is unknown;
  `ProviderAccepted` records a positive-duration ban acknowledgement and rule ID,
  **not measured device disconnection**. `Expired` means the encoded credential
  bound elapsed, not an observed provider action. Local state does not recall
  previously issued tokens instantly. These states are private participant data,
  never a public directory or broadcast event containing silent viewers.
- Retrying a lost acknowledgement first queries existing bans. A missing rule
  may cause another positive-duration ban for the same retired channel/UID.
  That duplicate is restrictive and bounded, not an access grant. Rule expiry
  is automatic; this path never deletes bans or bans an entire shared project.
- UID allocation uses current reads of both participation and durable retirement
  records under the room lock, not an earlier request snapshot. Retirement rows
  must be retained for the broadcast's lifetime; do not purge them while the
  channel can still issue credentials.
- GET reconciliation is best-effort. The official [ban-list reference](https://docs.agora.io/en/api-reference/api-ref/rtc/get-ban-rule-list.md)
  describes `ts` as a UTC expiry string but its example uses `{}`; it does not
  guarantee ISO formatting. We recognize only full timestamps that parse safely,
  and require matching project/channel/numeric UID, no IP restriction, join-ban
  privileges, a valid rule ID and sufficient remaining expiry before reusing an
  acknowledgement. Unrecognized/malformed/expired rules or failed GETs do not
  starve bounded repeated POSTs for the same permanently retired channel/UID.
  POST acknowledgements are validated too. No response bodies or HTTP exception
  details are logged. This parsing policy is not live provider-shape evidence.

## Runtime secrets and disabled provider adapter

The adapter requires an explicit `agora_broadcast_removal_enabled` runtime opt-in
and server-only `agora_rest_customer_id` / `agora_rest_customer_secret` runtime
configuration. These are REST customer credentials, **not** the App Certificate.
Use protected runtime configuration/secret injection. Never commit their values,
return them to clients, or log HTTP exceptions/responses. App Certificate remains
in the existing encrypted Password field. No provider call or configuration
change was made to implement this ticket; tests mock outbound HTTP.

## Required launch evidence

Measure Android/iOS first-render callbacks, failed-entry cutoff, hostile audience
publication/profile changes, and removal/termination with old tokens and clients
that ignore application events. Measure provider timeouts and recovery. Until
then, effective provider enforcement is unverified. Keep Live off and paid entry
disabled; do not substitute UI disconnection or renewal denial for these checks.

Provider contracts: [positive-duration bans](https://docs.agora.io/en/api-reference/api-ref/rtc/create-ban-rule)
and [channel-management OpenAPI](https://docs.agora.io/openapi/rtc/channel-management.en.yaml).
