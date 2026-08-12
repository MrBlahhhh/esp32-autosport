# Vendored firmware — do not edit

`r53_shiftlight_wideband/` is a **verbatim copy** of the sketch that runs in the
MINI today:

| | |
|---|---|
| Source | `C:\Projects\mini-r53-logger\firmware\esp32_shiftlight_wideband\` |
| Copied | 2026-08-12 |
| `main.cpp` SHA-256 | `c14898208980dce974e1425ba2003d7bc0eb4f4379c29b3275f333818a58fba5` |

It is here for one reason: `gen/simulate_firmware.py` builds it as the **control**
for the firmware-in-the-loop studies. Before the harness is allowed to make any
claim about the new board, it has to agree that this — known-good, on the
hardware it was written for — is clean. Study 1 is that check.

Keeping the copy in this repository means a study can never write to
`mini-r53-logger`. Nothing in `gen/` or `fwsim/` opens a path outside this tree.

**Do not fix anything in here.** A finding against this copy is a finding about
porting it, not a bug to patch — the port lives in
[`../esp32_shiftlight_wideband/`](../esp32_shiftlight_wideband/). If the R53
firmware genuinely changes, refresh the copy and update the hash above:

```sh
cp /c/Projects/mini-r53-logger/firmware/esp32_shiftlight_wideband/src/main.cpp \
   firmware/vendor/r53_shiftlight_wideband/main.cpp
sha256sum firmware/vendor/r53_shiftlight_wideband/main.cpp
python gen/simulate_firmware.py --only control     # must still be clean
```
