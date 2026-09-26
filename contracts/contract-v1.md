# Local runtime contract v1

All requests and responses are UTF-8 JSON. Business writes require a globally
unique `action_id` and may include `expected_revision`. Retrying the same action
returns the byte-equivalent logical result; a stale revision returns HTTP 409
and stable code `REVISION_CONFLICT`. Business success creates exactly one domain
event in the same SQLite transaction. UI telemetry is a separate event kind.

Bootstrap responses expose an ETag and contain no bulk resource rows. Dataset
resources are fetched with `offset` and `limit` (maximum 200), and return a
`page` object. Apps reject unsupported `contract_version` values.

Stable error codes include `REVISION_CONFLICT`, `PRICE_CHANGED`,
`INVENTORY_CHANGED`, `SESSION_NOT_READY`, `SIMULATED_NETWORK_FAILURE`,
`RESOURCE_NOT_FOUND`, `INVALID_ACTION`, and `UNKNOWN_ACTION`.

