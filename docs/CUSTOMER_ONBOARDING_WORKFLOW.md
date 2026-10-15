# Customer onboarding workflow

Use `central_partners` and `central_capabilities` as the draft customer/capability records. Do not create a separate wizard-only store. A wizard can save progress in those same records; creating a partner or draft capability must not start processing.

1. Select or register the verified Radius customer, company and plant; record source trading-partner identity separately.
2. Choose EDI type and direction from the candidate catalog. Mark an adapter available only after its contract and tests exist.
3. Select VAN or DIRECT. Infer the route strategy and show what the app will observe, transform or publish.
4. Enter independent Radius and partner folders, file pattern, format/version, ownership, environment and transport.
5. Choose a published mapping revision and verified customer cross references when translation is required.
6. Configure poll/schedule, retry, retention, alerts and expected acknowledgments after those services exist.
7. Validate the Radius-side active flag, format, filename/sequence, XLink option, path, permissions, mapping proof, adapter, environment and worker isolation. Show each failed check.
8. Run a no-post sample preview. For direct inbound API, use the current TEST-only payload preview.
9. Record an approval event with the exact configuration/mapping digests.
10. Enable only the approved processing mode. Any change to effective route or mapping invalidates the previous approval; the old active revision continues until replacement is approved.

Current UI provides draft partner/capability registration, editing and guarded read-only observation activation. The guided wizard, complete preflight, approval workflow and automated direct processing remain planned. Niagara's sample still lacks required address/date/unit and verified item references; its mapping is not ready to activate.
