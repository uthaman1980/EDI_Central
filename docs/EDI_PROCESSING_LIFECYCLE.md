# EDI processing lifecycle

One source file can contain several documents. Assign a file/artifact ID and a correlation ID per document. Record each stage separately; a successful earlier stage must not imply a later one.

| Route | Current first stage | Required future evidence chain |
| --- | --- | --- |
| VAN inbound | Radius-ready file observed read only | Radius pickup/import, XLink record result, business document reference |
| VAN outbound | Radius output observed read only | Provider pickup proof, transport receipt, EDI acknowledgment if supplied |
| Direct inbound | Customer source parsed and mapped | Validation, approved TEST payload or Radius file, posting/import, business result |
| Direct outbound | Radius original parsed | Customer mapping, generated artifact, atomic publication, transfer/receipt/ack |

Suggested independent dimensions: `file_state`, `mapping_state`, `radius_import_state`, `business_state`, `delivery_state`, `ack_state`. Unknown remains unknown. Record attempts, error category, stage, actor, timestamps and exact source digest. Use an idempotency key scoped to environment, partner, direction, EDI type and business/control identity. If a Radius POST times out, hold for reconciliation before retry. If an outbound transfer times out, check destination/receipt before resending.

Current implementation: `service.py` already saves SO/SR attempts and guards duplicate order creation; `central.py` observes stable files and leases capability scans. Neither is a full cross-route document queue. The legacy inbox watcher moves app-owned files; it must not be reused against VAN/Radius-owned paths. A 997/999 or file disappearance cannot prove a Radius business result.
