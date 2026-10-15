# Routing matrix

| Direction | Route | Strategy | Current executable behavior | Completion evidence |
| --- | --- | --- | --- | --- |
| Inbound | VAN | `PASSTHROUGH_TRACK` | Read-only observation of verified Radius inbound folder | `RADIUS_FILE_OBSERVED`; posting remains unverified |
| Inbound | DIRECT | `TRANSLATE_TO_RADIUS` | Published TEST mapping can prepare and explicitly submit through the existing Radius REST SO/SR service; no intermediate 850 file | Saved source + pinned mapping + actual API request/response + SO/SR numbers; native file handoff remains separate |
| Outbound | VAN | `PASSTHROUGH_TRACK` | Read-only observation of Radius outbound folder | `PICKUP_UNVERIFIED` until independent provider proof |
| Outbound | DIRECT | `TRANSLATE_FROM_RADIUS` | 856 reference/line mapping to customer CSV/JSON preview/download | Mapped output is not delivery; automated publishing, full ASN serializer and receipts require their own contracts |

Route class is independent of transport. A VAN may use SFTP or a share, and a DIRECT customer may use AS2; neither changes the transformation rule automatically. A Radius-ready provider file must not be translated by default. Existing Pepsi fixed 850 REST processing is a legacy application route and is not mislabelled as native Radius/VAN pass-through.
