# Menu reorganization

The existing Flask/Jinja layout uses a collapsible, left-side menu built with native `details` elements. Groups contain only implemented routes; disclosure and links work with the application's `script-src 'none'` policy. The current section opens automatically, and small screens show the menu above the page content.

| Group | Working links now | Future additions after implementation |
| --- | --- | --- |
| Dashboard | Enterprise overview; Inbound 850 overview | Processing health with unified data |
| EDI Monitor | Transactions & files; Radius 856 processing; Inbound 850 file history | Inbound/outbound document views; acknowledgments |
| Customers & Partners | Trading partners; Legacy 850 customers | Unified customer detail and onboarding |
| EDI Configuration | Capabilities & routes | Message types, schedules, connection profiles |
| Mapping Studio | Mapping profiles; Inspect inbound 850; Preview outbound 856 | Format definitions, shared master data |
| EDI Operations | Process inbound 850; Exceptions; Observer jobs | Durable queue, controlled retries, archive |

The labels distinguish the legacy 850 dashboard from enterprise-wide metrics and Radius 856 processing from customer delivery. Existing URLs and functional controls remain intact. No placeholder link implies an unbuilt feature is operational. When a unified transaction model exists, retire duplicate legacy links through redirects and preserve bookmarks.

Next UI work: consistent status vocabulary, enterprise transaction filters, customer context, accessible data grids, and role-aware controls after backend RBAC exists. See `IMPLEMENTATION_ROADMAP.md` for sequence.
