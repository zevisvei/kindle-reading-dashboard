# Kindle USB networking (usbnet) + Windows troubleshooting

To reach the Kindle over SSH you need **USB networking (usbnet)** enabled on the jailbroken device. The host then talks to the Kindle at `192.168.15.244` (host gets `192.168.15.201`), SSH on port 22, user `root`, default password `kindle`.

## "Unknown USB Device (Device Descriptor Request Failed)"

A USB **enumeration** failure (device IDs read as `VID_0000&PID_0002`, problem `CM_PROB_FAILED_POST_START`). This is **hardware-level**, not a driver issue. Fixes, in order of likelihood:

1. **Swap the USB cable.** A charge-only or failing cable is the #1 cause.
2. **Plug directly into a rear motherboard port** — no hub/extender. Prefer USB 2.0 (black) over 3.0 (blue) for these old gadget devices.
3. **Reboot the Kindle** (hold power ~40 s) — the USB gadget mode can hang.
4. **Disconnect other devices** on the same hub that may starve power or confuse enumeration.

## "Serial USB device (COM port)" instead of a network adapter (Windows 10/11)

With usbnet enabled, the Linux RNDIS gadget's USB class pair (02/02) makes Windows misclassify it as a serial port. Install **Marco77's signed RNDIS driver** (`kindle_rndis.inf`, VID_A4A2&PID_0525):

1. Download/extract the driver package.
2. Run the admin batch file to register its self-signed certificate into the trusted-publisher store.
3. In Device Manager, "Update driver" on the *Serial USB device*, pointing at the extracted folder.
4. Assign the host a static IP `192.168.15.1` (or `.201`) on the new "Kindle USB RNDIS Device" adapter.

Source: https://www.mobileread.com/forums/showthread.php?p=3283986

Once it enumerates as **"Kindle USB RNDIS Device (USBNetwork enabled)"** (no yellow triangle), `192.168.15.244:22` is reachable. Stale `ROOT\NET\000x` adapters in an *Error* state are harmless ghosts.

## Quick checks (Windows / PowerShell)

```powershell
Test-NetConnection 192.168.15.244 -Port 22          # SSH reachable?
Get-PnpDevice | ? { $_.FriendlyName -match 'Kindle|RNDIS|Descriptor' }
Get-NetIPAddress -AddressFamily IPv4 | ? IPAddress -like '192.168.15.*'
```

## SSH notes

- The Kindle's busybox sshd has **no SFTP subsystem**. Transfer files by streaming through `cat` (see [`ksh.py`](../ksh.py) `get`/`put`).
- Paths with spaces and apostrophes (e.g. a folder named `dshnr, g'yyms`) must be shell-quoted with single-quote escaping (`'\''`), or `cat` silently returns 0 bytes.
