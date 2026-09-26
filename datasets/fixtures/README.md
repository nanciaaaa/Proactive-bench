# Research fixture contract

`research-seed-v1.json` is the temporary data overlay used by all six apps.
It contains synthetic product records plus public geographic facts; it does not
contain copied account data, reviews, audio, orders, or personal
information.
Article and topic bodies are newly written synthetic examples.

The Controller API does not depend on fixture wording. A later injector may
replace this file while keeping the following contract:

- retain `schema_version`, `origin`, `notice`, and the fourteen resource keys;
- encode each record as `[title, subtitle, price, available]`; articles and topics may add fifth `body`, articles may add sixth `topic_id`, and playlists may add fifth `track_ids` (a list of song IDs);
- keep `title` and `subtitle` as strings and `price`/`available` as integers;
- bump `VERSION` in `local-controller/datasets.py` whenever packaged data changes;
- rebuild the generated catalog and reset the instance after replacement.

The public item response remains:

```json
{
  "id": "app-resource-00000",
  "app_id": "app",
  "resource": "resource",
  "title": "display title",
  "subtitle": "display subtitle",
  "meta": "deterministic metadata",
  "price": 0,
  "available": 0,
  "rank": 0,
  "topic_id": "optional topic ID for an article",
  "track_ids": "optional JSON song ID list for a playlist"
}
```

Do not add source-specific or personal fields to the endpoint. Normalize future
sources into this fixture contract so app, action, reset, snapshot, and test
interfaces remain unchanged.

For inspection-time updates, post normalized rows to
`POST /local/v1/apps/{app_id}/data/{resource}` with an `items` array matching
`contracts/data-injection.schema.json`. Injected rows are stored in the
instance state database, take display priority over packaged rows with the same
ID, and disappear on the normal instance reset. Bootstrap and runtime read
paths do not change.
