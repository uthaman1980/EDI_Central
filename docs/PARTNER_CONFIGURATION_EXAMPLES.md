# Configuration examples (synthetic TEST data)

These examples are illustrative drafts. Paths are placeholders under an operator-approved TEST root; they are not statements about actual Radius configuration.

| Partner | Capability | Route | Folder roles | State |
| --- | --- | --- | --- | --- |
| `SYNTH_A` | Inbound 850 | VAN | Radius inbound `D:\ApprovedTest\A\850\in` | Draft until Radius activation and folder contract verified; then read-only observation |
| `SYNTH_A` | Outbound 856 | DIRECT | Radius outbound `D:\ApprovedTest\A\856\radius`; partner delivery `D:\ApprovedTest\A\856\partner` | Draft; no verified translator |
| `SYNTH_B` | Outbound 856 | VAN | Radius outbound `D:\ApprovedTest\B\856\out` | Read-only observation when approved; pickup unverified |

The current real-looking PEP01 850 reference file remains outside test fixtures. Its existing fixed 850 REST creation profile is visible in EDI Central as an existing adapter and continues through the proven workbench. **TO VERIFY:** actual customer paths, Radius-active flags, formats, and partner route ownership before migrating it to the central pipeline.
