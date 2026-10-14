# Routing matrix

| Direction | Route | Strategy | Current executable behavior | Completion evidence |
| --- | --- | --- | --- | --- |
| Inbound | VAN | `PASSTHROUGH_TRACK` | Read-only observation of verified Radius inbound folder | `RADIUS_FILE_OBSERVED`; posting remains unverified |
| Inbound | DIRECT | `TRANSLATE_TO_RADIUS` | Draft configuration only; legacy X12 850 REST adapter remains separate | Requires verified Radius file writer/import result |
| Outbound | VAN | `PASSTHROUGH_TRACK` | Read-only observation of Radius outbound folder | `PICKUP_UNVERIFIED` until independent provider proof |
| Outbound | DIRECT | `TRANSLATE_FROM_RADIUS` | Draft configuration only | Requires Radius parser, partner serializer, distinct delivery path and receipt policy |

Route class is independent of transport. A VAN may use SFTP or a share, and a DIRECT customer may use AS2; neither changes the transformation rule automatically. A Radius-ready provider file must not be translated by default. Existing Pepsi fixed 850 REST processing is a legacy application route and is not mislabelled as native Radius/VAN pass-through.
