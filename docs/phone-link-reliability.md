# Phone link reliability — root causes and what the app does about them

Investigated 2026-09-29/30 on the reference setup: iPhone 16 Pro Max (iOS 26.7)
on USB to a Windows 11 PC, go-ios 1.3.2 userspace tunnel, the WebDriverAgent
runner driven by `video_drop/phone/` (vendored from SideTap). Evidence lives in
`.state/link-health.log` (10 s poll of tunnel + WDA), `.state/link-watch.log`,
the driver's `.state/phone/*.log` (SideTap's own `.state/*.log` before the
cutover), and the Windows event log
`Microsoft-Windows-Kernel-PnP/Device Management`.

## Three different failures were being treated as one

### 1. The phone drops off the USB bus (most drops)

Windows recorded the phone as *surprise removed* (Kernel-PnP event 1010,
"reported as missing on the bus") **35, 137, 84 and 11 times** on
2026-09-27, -28, -29 and -30. Every overnight "down"/"none" transition in
`link-health.log` matches one of those events to within a few seconds
(00:05:14, 00:45:22, 01:41:29, 01:44:53, 05:13:12, 05:23:33 …), with nobody at
the desk. Each removal resets the tunnel (the rsdPort climbs by one per
re-enumeration: 54130 → 54169), orphans the WDA runner, and used to trigger a
full `up()` that sometimes could not negotiate a route until a manual replug.

The PC side of this:

* USB selective suspend is **enabled** on both AC and DC in the active power
  plan (`powercfg` subgroup 2a737441…, setting 48e6b7a6…).
* The phone sits on the ASMedia chipset controller (`PCI\VEN_1022&DEV_43D5
  SUBSYS_11421B21`, "AMD USB 3.10 eXtensible Host Controller"), root hub
  `USB\ROOT_HUB30\5&4087d53&0&0`. The CPU's own controller (`DEV_149C`) is a
  different set of ports.

Things only the owner can change (the app never changes system settings):

1. Turn USB selective suspend **off** (Power Options → Advanced → USB settings),
   and untick "Allow the computer to turn off this device to save power" on the
   USB Root Hub (USB 3.0) the phone is on.
2. Move the cable to a port on the CPU controller (the rear ports that are
   not on the chipset), or at least a different port; try another data-capable
   cable. A powered hub is not a fix.
3. On the phone, keep it unlocked while posting; iOS ends the runner ~15 min
   after the screen sleeps.
4. Make sure the port actually charges the phone. On 2026-09-30 every link
   stall happened with the battery at 1–23%, and `ios batteryregistry` on the
   chipset port read `IsCharging: true` while the phone **drained at 2.4 A**
   with TikTok or YouTube on screen (pack voltage 3.33 V at 1%). Charge it on
   a wall charger first and prefer a port that can supply the load (a rear
   port on the CPU controller, a powered hub, USB-C PD). The setup checklist's
   "iPhone charging over USB" row shows the live reading; the link experiments
   record it every 15 s and print a `power:` line when it drains while plugged in.

### 2. WebDriverAgent wedged by a playing video (looked like "WDA down")

TikTok's For You feed, Instagram Reels and its reel/cover editor, Edits while a
clip plays, and Threads' autoplaying home feed stop answering iOS accessibility
requests. WDA serves one request at a time, so `/source`, `/screenshot` and even
`/status` queue behind the stuck one and time out. The tunnel is fine. Putting
SpringBoard in front (`ios launch com.apple.springboard`) releases it; a restart
on top of the stuck runner fails with XCTest error 103.

On 2026-09-30 10:47 a tap in Edits' editor wedged WDA; 28 s later go-ios logged
"lost connection to testmanagerd. the test-runner may have been killed"; two
minutes later every new RSD connection through the (still running) tunnel timed
out ("Connect to remote failed: operation timed out"). No USB event was logged.
A fresh daemon could not negotiate a route either; only a replug fixed it.

### 3. Recovery that made things worse

`phone_harness up()` decided from a single 5 s probe (`tunnel ls` + TCP + `ios
image list`). A slow answer under load killed a healthy daemon. And every
posting script called `up()` itself, so several processes could restart the same
link at once (SideTap's viewer has its own healer too).

## What the app does now

`video_drop/link_supervisor.py` is one detached process (started by the server,
or by `python -m video_drop.link_supervisor`) that owns tunnel, runner and
forwards. Every 5 s it probes WDA `/status` first (answers / accepts-but-silent /
refused), then the runner, forward and daemon processes, the phone on USB (`ios
list`), the `tunnel ls` entry, whether any other process landed an action, and
whether a script declared the phone busy; the slow route probe (`ios image
list`) runs only every 60 s when ready, every 15 s when down, and never while a
wedge is being judged. Then:

| Situation | Action |
| --- | --- |
| Phone off the bus | Wait. The go-ios daemon re-creates the tunnel by itself when the phone returns (seen in `tunnel-kernel.err`: "stopping tunnel" → "start tunnel" a second after every re-enumeration). Nothing is killed. |
| Tunnel listed, runner dead / forwards dead | Restart only the runner / forwards, with backoff (3, 10, 30, 60 s) — but never while the route is not answering (a runner cannot reach testmanagerd without it). A silent forward with no runner behind it counts as dead, not wedged. |
| WDA accepts but never answers, no other process landing actions | After 2 consecutive silent probes (~10 s), press Home. **iOS kills the runner ~39 s after a wedge** (10:47:40→10:48:18 and 11:24:32→11:25:11 on 2026-09-30, tunnel healthy both times), so this is the one threshold a busy window never stretches. Never restart. A wedge that survives two Home presses 45 s apart restarts the runner only. |
| WDA silent **and** lockdown silent while usbmuxd still lists the phone (3 consecutive raw probes, `video_drop/pipe_stall.py`) | A **pipe stall**. No Home press (`ios launch` would only hang). The recovery ladder: wait 30 s; restart Apple Mobile Device Service; restart the iPhone's USB device node; each step verified by lockdown answering again and logged with its seconds (`ladder` in `link-events.jsonl`, `lastStall` in `link-status.json`); only then `needs-replug`. The two privileged steps go through the USB recovery helper (`docs/usb-recovery-helper.md`); without it the ladder says so and asks for the replug. States `pipe-stall`, `pipe-stall-service`, `pipe-stall-usb`. |
| Runner dead, lockdown answering | A cheap runner restart in seconds (the pipe probe's `ok` lifts the route hold). Never a tunnel action, never a replug. |
| Route dead for 3 consecutive probes with the phone on the bus, lockdown not yet judged | Leave the daemon alone for **5 minutes** (15 while busy): every such death so far followed a heavy transcode and a fresh daemon could not negotiate either. Then restart the daemon **once**. If the new one cannot route either: state `needs-replug`, "Unplug and replug the phone" — and keep probing, because a route that answers again clears it. |
| SideTap's viewer is running | Stand by: probe and log, take no action, say why. Its `_heal_loop` is a second healer (`admin._UP_LOCK` is in-process only), and at 11:28:13 a concurrent `up` replaced a healthy tunnel with one that never connected. |
| A daemon or runner someone started by hand | Adopt it (write its pid file under `.state/phone/`) instead of starting a second one. |
| A script declared busy (upload, export) | Route/entry thresholds ×3, route probes spaced out, nothing restarted on one miss. |

The runner's last error is turned into a plain hint ("Unlock the phone and
allow UI automation…", "re-sign WebDriverAgent…"). Status: `.state/link-status.json`
(shown in the app's Phone panel via `/api/link`); every decision:
`.state/link-events.jsonl`; the go-ios logs (`.state/phone/*.log`) are copied to
`.state/link-logs/` before each restart (20 kept per process).

Posting scripts (`scripts/phone_youtube.py`, `phone_tiktok.py`,
`phone_threads.py`) now declare busy windows around preparation and after the
final tap (90 s + 1.5 s/MB for the in-app upload), read pixels through go-ios
(never WDA) on video surfaces (YouTube trim, TikTok editor/post screen), and ask
the supervisor for recovery instead of restarting the link themselves.

**Every playing screen is read by pixels and OCR only (2026-10-02).** The
2026-10-01 17:22 soak lost the phone off USB in the YouTube flow: the driver's
video guard (`config.AX_VIDEO_APPS`) listed TikTok alone, and YouTube's trim and
editor play the clip. The guard now lists every app with a playing surface
(TikTok, YouTube, Instagram, Edits, Threads, Facebook) and covers both
accessibility routes, `/source` (`ui_tree()`) and `/wda/activeAppInfo`
(`current_app()`, `wait_for_app()`). While the noted app's screen is visibly
moving (two go-ios frames), both raise `VideoSurfaceError` without asking WDA.
`choose_share_app()` notes the destination before its tap. The flows then:

| Screen | Read by |
| --- | --- |
| YouTube Home feed (previews autoplay), You tab | OCR rows (`youtube_nav.visible_rows`); You is the one on the tab bar |
| YouTube trim, Processing, editor | OCR words ("Crop your video", "Processing…", "Swipe up to edit") and the one "Next" on the bottom quarter |
| Share hand-off to a destination that opens playing | A refused `activeAppInfo` counts as "left the share sheet"; the destination's first screen proves the app |
| Instagram feed, Reel or story at the account check | OCR: a story (its "Send message" bar) is swiped closed, anything else taps the Profile tab at its measured place |
| Threads composer, receipts, Instagram/Edits steps | Wait up to 15 s for a still screen, then stop with a clear message; exact text is never taken from OCR |

OCR is Windows' built-in engine (`video_drop/ocr`); its rows are typed
`OcrText`, so they never satisfy a check that needs a real control. Needs
device check: the OCR words above come from the screens' accessibility labels,
which these screens draw as visible text. The soak on the front port is the proof.

### Running it

```
cd D:\Documents\GitHub\video-drop
python -m video_drop.link_supervisor            # the live supervisor (one instance; exits if one runs)
python -m video_drop.link_supervisor --observe  # probe and log "would ..." only, never act
```

The app server starts the live one itself (`link_supervisor.ensure_running`),
detached, so a server restart or a script exit never takes the link down.
Close SideTap's viewer first; the supervisor stands by while it runs.

Scripts: call `video_drop.phone_link.wait_ready(timeout)` before the first
phone action and check `["state"] == "ready"`; on a `WDAError`, call
`phone_link.recover(...)` (it presses Home, waits, then asks the supervisor via
`.state/link-request` and waits again — it never runs `up()` while a supervisor
is alive). Wrap long phone work in `phone_link.busy(reason, seconds, linger=...)`.
Never call `phone-harness up` from a script.

## The pipe stall: automated recovery, replug last

When everything that crosses the USB data pipe dies while the phone stays on
the bus (case 2 above, measured 2026-09-30
— lockdown included, so not a WDA wedge), the supervisor no longer waits five
minutes and asks for a replug. It names the stall with three raw probes and
walks a recovery ladder — wait, restart Apple Mobile Device Service, restart
the iPhone's USB device node — verifying each step by lockdown answering, and
shows the replug message only when all of it failed. The two privileged steps
run through a small on-demand SYSTEM task the installer registers once with
UAC consent (`scripts\install_windows.ps1 -InstallUsbHelper`). Design,
protocol and threat model: `docs/usb-recovery-helper.md`. The ladder's log is
also the measurement the root-cause analysis asked for: which step brings a
real stall back, and after how long.

Prevention on the scripts' side: `phone_link.video_in_front()` (two go-ios
frames; a moving main region means "no WDA tree reads here") and
`WDA_SETTINGS_PROFILE=lean|media` (bounded accessibility snapshots, shallow
trees, no MJPEG keys) for the experiment agent's A/B runs.
`scripts/link_experiment.py` runs the experiments; the supervisor's
`--tunnel-mode kernel|pmd3` adopts an externally started tunnel and
`--no-mjpeg-forward` leaves :9100 alone for those A/B runs.
