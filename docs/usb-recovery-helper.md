# USB recovery helper and the pipe-stall ladder

What happens when the iPhone's USB data link stalls, how the app recovers it
without a replug, and why the one elevated piece cannot be turned against the
PC. Companion to [phone-link-reliability.md](phone-link-reliability.md) (the
supervisor).

## The failure this handles

A *pipe stall*: lockdown, WebDriverAgent and the
go-ios tunnel all stop moving bytes within one keepalive window while Apple
Mobile Device Service (usbmuxd) still lists the phone. Roughly 90 s later
Windows sometimes logs Kernel-PnP 1010 "surprise removed". Until 2026-09-30
only a physical replug recovered it, and the supervisor's answer was to press
Home into it, wait five minutes, restart a daemon that could not negotiate
either, and then say "unplug and replug the phone".

From WDA's side a stall is indistinguishable from a video wedge (the forward
accepts, nothing answers). The difference is one hop lower: lockdown.

## 1. Detection

`video_drop/pipe_stall.py` runs three raw probes from `link_probe`, each
bounded to 2 s, in-process, read-only on the phone:

| Probe | Says |
| --- | --- |
| usbmux `ListDevices` | the phone is (still) in AMDS's device table |
| lockdown `QueryType` on :62078 | the USB data pipe carries bytes both ways |
| WDA `GET /status` on :8100 through a fresh usbmux `Connect` | the pipe reaches WDA without the forward process |

`classify_pipe` turns one round into `ok` (lockdown answered, or WDA did over
the same pipe), `stalled` (phone listed, **both** lockdown and WDA timed out),
`no-device` (not listed: unplugged or mid re-enumeration) or `unknown` (usbmuxd
itself silent, or a non-timeout error such as a refused connect; never counted
as a stall).

The supervisor (`link_supervisor.Runner.observe`) only runs the probe while
WDA through the forward is *not* answering. An answering WDA already proves
the pipe, so a healthy link pays nothing; a broken one pays at most ~4 s per
tick. The Decider needs `Policy.stall_probes` (3) consecutive `stalled`
observations before it calls a stall (×3 while a script declared the phone
busy). Three outcomes, three different repairs:

| Observation | Meaning | Action |
| --- | --- | --- |
| WDA silent, lockdown answers | WDA-only wedge (an app holding the accessibility server) | press Home, as before |
| WDA silent, lockdown silent, phone listed | pipe stall | the ladder below; no Home press (`ios launch` would only hang through the dead tunnel) |
| phone not listed | unplugged / re-enumerating | wait, as before |

A dead runner with lockdown answering is a cheap runner restart (seconds):
the pipe probe's `ok` lifts the route hold that used to wait for the next
`ios image list`. It is never a tunnel action or a replug.

## 2. The ladder

Once a stall is called, `Decider._ladder` walks these steps. Every step is
verified the same way: lockdown answering again ends the ladder, whatever the
helper reported. Each step's seconds and outcome are recorded and written to
`.state/link-events.jsonl` (`ladder` field) and `link-status.json`
(`lastStall`), which is also the measurement the root-cause work asked for:
whether an AMDS restart or a device-node restart recovers a real stall, and
after how long.

| Step | State (`link-status.json`) | Message | Bound |
| --- | --- | --- | --- |
| (a) wait | `pipe-stall` | "…stopped answering while it stays plugged in. Waiting a moment in case it clears by itself…" | `stall_wait` 30 s (×3 while busy) |
| (b) restart Apple Mobile Device Service | `pipe-stall-service` | "…restarting Apple's USB service…" | helper call, then `stall_verify` 45 s for lockdown |
| (c) restart the iPhone's USB device node | `pipe-stall-usb` | "…resetting the phone's USB port (like a replug, without touching the cable)…" | helper call, then `stall_usb_verify` 60 s; the phone briefly leaving the bus during this step is expected, not a replug |
| (d) replug | `needs-replug` | "Unplug and replug the phone…" plus, when the helper is missing, how to install it | until lockdown answers or the phone leaves the bus |

On recovery the supervisor restarts the port forwards (they were bound to the
stalled connection), gives the go-ios daemon its full grace to relist the
tunnel, and the normal path rebuilds the runner. The ladder record is kept
while a failed stall persists so the helper is not called in a loop; it ends
when lockdown answers or the phone is unplugged. A helper that refuses or
fails a step moves the ladder on immediately; a helper that gives no reply in
time leaves the verdict to lockdown.

Observe-only supervisors (`--observe`) log `would restart_apple_service` and
walk the same timings without calling the helper.

Related published behaviour the ladder respects: go-ios's
tunnel manager does not notice a dead-but-listed tunnel by itself (#765), so
the supervisor probes lockdown rather than waiting for the daemon; iOS 26.2+
keeps one RSD peer per host and evicts the other (pymobiledevice3 #1994), so
nothing here ever starts a second dialer beside the daemon; and the AMD
chipset controller's dropout class (U1) is exactly what step (c) measures — a
node restart may or may not recover it, and the ladder log will say which.

## 3. The helper

Steps (b) and (c) need administrator rights. The app never runs elevated, so
they run inside a **scheduled task that runs as SYSTEM, on demand only**,
registered once by the installer with the user's consent through UAC:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -InstallUsbHelper
# later, to remove it:
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -UninstallUsbHelper
```

Both are idempotent. The installer's regular run and `-CheckOnly` only *report*
whether the helper is there; the setup checklist row **USB recovery helper
installed** (read-only: the script file plus `schtasks /Query`) does the same
in the app and in `python -m video_drop.setup_report`.

### Files

| Where | What |
| --- | --- |
| `video_drop/usb_helper/usb_helper.ps1` | the SYSTEM-side script (source in the repo; the task runs the installed copy) |
| `video_drop/usb_helper/install_usb_helper.ps1` | the elevated install/uninstall/status step |
| `video_drop/usb_helper/__init__.py` | the client the supervisor uses (`request`, `status`) |
| `%ProgramData%\AutoIphoneUploader\usb-helper\` | installed copy: `usb_helper.ps1`, `config.json` (installing user's SID, version, script hash), `requests\`, `results\`, `usb-helper.log`, `last-action.txt` |
| task `\AutoIphoneUploader\UsbRecovery` | runs `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <installed usb_helper.ps1>` as SYSTEM; no trigger; one instance; 3-minute limit; hidden |

### Protocol

1. The client writes `requests\<nonce>.json` = `{"command": "restart-apple-service" | "restart-usb-device", "nonce": "<16 hex>", "issued": <unix s>}`.
2. It starts the task with `schtasks /Run /TN \AutoIphoneUploader\UsbRecovery`.
3. The helper reads at most five request files, oldest first, and for each
   checks: plain file (no reparse point), under 1 KB, name is a nonce, **NTFS
   owner is the installing user's SID** from `config.json`, body nonce matches
   the file name, `issued` within the last 120 s, `command` is one of its two
   table entries, and 30 s have passed since its previous action. Any failed
   check is refused (logged, result `ok: false`, request deleted).
4. It runs the fixed commands, writes `results\<nonce>.json`
   (`{nonce, ok, command, detail, started, finished}`) and deletes the request.
5. The client waits up to 45 s for the result. No result is *unknown*, never a
   success: the supervisor's verdict is lockdown answering, not this reply.

The two commands, as implemented:

* `restart-apple-service`: `Stop-Service 'Apple Mobile Device Service' -Force -NoWait`, wait up to 20 s; if the service will not stop (a wedged device thread does not honour the stop), `taskkill /F /IM AppleMobileDeviceService.exe` (fixed image name); `Start-Service`; wait up to 20 s for Running.
* `restart-usb-device`: `Get-PnpDevice -PresentOnly` filtered to `USB\VID_05AC&PID_12A8\*` without `&MI_` (the iPhone's composite parent node, every present one), then `pnputil /restart-device "<that instance id>"`; on a non-zero exit (pre-2004 Windows) `Disable-PnpDevice` + `Enable-PnpDevice` on the same id.

### Why PowerShell, not Python under pythonw

The helper executes as SYSTEM, so everything it loads must be unwritable by the
user, or a standard-user compromise becomes a SYSTEM one. A Python helper would
run an interpreter and site-packages from wherever the app's venv lives — the
user's profile or the checkout, both user-writable — or need a second, system-
wide Python the installer does not control. Windows PowerShell 5.1 ships with
the OS under `%SystemRoot%`, `Restart-Service`, `Get-PnpDevice`,
`Get-Acl` and `pnputil` are built in, the whole script is ~200 lines a
reviewer can read top to bottom, and it needs no dependency. The Python side
stays what it is: a client that writes one file and runs `schtasks`.

## 4. Threat model

Assets: the machine's SYSTEM privilege; the phone link; the iPhone (nothing
here can touch the phone's contents — both commands are host-side).

Who can reach the helper: any process running as the installing user (the
task's security descriptor `D:(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;<user SID>)`
lets only SYSTEM, Administrators and that one account read or start it; the
`requests` folder grants only that account Modify; other local users can read
the folder and nothing else). So the attacker to reason about is malware
already running as the app's user, and the question is what it gains.

**No code execution.** The task's action is a fixed command line to
`powershell.exe -File` on a script in a folder whose inheritance is cut and
whose ACL is SYSTEM + Administrators full, Users read-execute, owner
Administrators. The script takes no parameters (`param()`), never reads
`$args`, never calls `Invoke-Expression`, `Start-Process`, `cmd.exe` or
`-Command`, never builds a command line from request text, and the tests in
`tests/test_usb_helper.py` pin every one of those facts against the source.
The installer refuses to install over a junction or symlink at any path it
writes and rewrites the ACLs and owner on every run, so a folder pre-created
by a user before installation ends up locked the same way.

**No arbitrary device control.** The only device-touching command restarts
the nodes Windows itself enumerates under `USB\VID_05AC&PID_12A8\` — Apple
iPhones. The instance id never comes from the request. There is no way to name
another device, another service, another image name or another switch. The
only service touched is `Apple Mobile Device Service` by literal name.

**Caller authentication.** Three independent gates: the task can only be
started by the installing user (Task Scheduler SD); the request file can only
be created by that user (NTFS ACL on `requests`); the helper checks the
request file's NTFS owner SID against `config.json`, which only administrators
can write. A file dropped by another user, copied in, or hard-linked in still
carries the wrong owner or fails the reparse/size/nonce checks.

**Replay and flooding.** Requests older than 120 s are refused; the helper
acts at most once per 30 s (`last-action.txt`, SYSTEM-owned) and the task
runs one instance at a time (`MultipleInstances IgnoreNew`) with a 3-minute
limit. The worst a compromised user account can do is restart AMDS or
re-enumerate the iPhone every 30 s: a denial of service against the phone
link that the user already owns, nothing beyond it.

**What it does not defend against.** An administrator: they can edit the
script or the config, but they could do anything anyway. A compromised
`powershell.exe` or Windows itself. Physical access.

**Auditing.** Every accepted or refused request is one line in
`usb-helper.log` (SYSTEM-owned, readable by everyone; truncated at 1 MB), and
the supervisor writes what it asked for and what came back to
`.state/link-events.jsonl`.

## 5. Prevention hooks for the flows

* `phone_link.video_in_front(ios_path)` — two go-ios screenshots
  ~300 ms apart; `.video` is True when a large share of the main region's
  pixels changed (`VIDEO_CHANGE_FRACTION`, bootstrap 0.08). Ask it before any
  `ui_tree()` on a screen that can autoplay (TikTok feed/editor, Reels,
  Shorts, Threads feed) and drive by coordinates and pixels when it says
  video. The thresholds are marked **TODO-to-measure** in `phone_link.py`;
  measure them with `scripts/record_screen.py` on each app's video and static
  screens before relying on them on the post path.
* `WDA_SETTINGS_PROFILE=default|lean|media` (env or `.env`) —
  `video_drop/phone/wda_profiles.py` fills in defaults for the `WDA_*` /
  `MJPEG_*` keys before `config` reads them; an explicitly set key always
  wins. `lean` (the A/B arm): no MJPEG keys, `accessibilityDeadline` 0.5,
  `snapshotMaxDepth` 20, no idle wait. `media` (sessions that will sit on
  video surfaces): depth 12, deadline 1.0, no idle wait, no MJPEG keys.
  Settings ride with the WDA session, so set the profile in the process that
  mints it (a posting script, or `scripts/link_experiment.py`, which mints a
  fresh session when its experiment carries env).

## 6. Setup rows

* **USB recovery helper installed** (required): script + task present.
* **iPhone USB path** (advice, never blocks): walks `DEVPKEY_Device_Parent`
  from the iPhone's USB node up to its PCI controller, read-only. The AMD
  500-series chipset controller (`DEV_43D5`, the documented dropout
  controller) or any hub in the chain (e.g. a VIA `VID_2109` hub) turns the row
  into "plug the iPhone straight into a rear port on the PC's own (CPU) USB
  controller (`DEV_149C` here), no hub". A CPU controller with no hub reads ok.

## 7. What is still unmeasured

The ladder has not yet run on a real stall (the phone was unplugged while this
was built). The first real stall will say, in `link-events.jsonl`, which step
brought lockdown back and after how many seconds — or that none did. Until
then `stall_wait`, `stall_verify` and `stall_usb_verify` are bounds chosen
from how long each step takes on a healthy PC, not tuned values, and the
video-surface thresholds are bootstrap numbers.
