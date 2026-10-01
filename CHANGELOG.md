# Changelog

All notable changes to Auto iPhone Uploader. Versions match the Git tags that
[release.yml](.github/workflows/release.yml) builds release zips from.

## 1.0.0 (current candidate 1.0.0-rc.4)

Post finished videos from a Windows PC through the real iPhone apps: YouTube
Shorts with title, description and a first-frame thumbnail; Instagram Reels
through Edits' 4K export with Facebook and Threads crossposts; TikTok. Post
now or Schedule into the apps' own schedulers, with Do Not Disturb during
runs, receipts read back from the apps, a self-healing USB link supervisor
and a setup checklist. The README's
[Requirements and limits](README.md#requirements-and-limits) and
[docs/launch-checklist.md](docs/launch-checklist.md) say what has been
verified on the reference phone.

### 1.0.0-rc.4 (2026-10-01)

#### Fixed

- The iPhone passcode is kept encrypted at rest with Windows DPAPI
  (`PHONE_PASSCODE_DPAPI`, readable only by your Windows account on this PC);
  `scripts\set_passcode.py` removes a plain-text line. Run it once to
  encrypt a passcode saved by an earlier version.
- `install_windows.ps1 -CheckOnly` exits 0 when it reports steps still to do,
  so the release build no longer stops on a clean machine (rc.3 published no
  download for this reason).

### 1.0.0-rc.3 (2026-10-01)

#### Added

- **Remove the video from the iPhone after it posts.** Posts save nothing to
  Photos, but every Instagram post leaves an Edits project holding the clip
  (often over a gigabyte). Once Instagram confirms the post, that one project
  moves to Edits' Trash, where it can still be restored. It is matched exactly
  (new since the upload, still "Untitled project", young enough); anything
  ambiguous leaves Edits alone. Setting on by default.
- **A calm first-run setup.** Five screens, one task each, that advance by
  themselves; commands sit in copyable console blocks behind "Show me how";
  optional items moved to **Extras** in Settings. System, light and dark
  theme toggle.
- **One-command install** with numbered progress, plain messages, a fix for
  every failure, and optional prompts for the passcode and the USB recovery
  helper (now recommended rather than required).
- **One owner of the iPhone at a time.** Every phone flow holds a lock, so a
  script run by hand, a slot post and a receipt read can never drive the
  phone together; a lock left by a crashed run is ignored.
- **YouTube's hidden detail rows are read with Windows' built-in OCR.**
  YouTube 21.38 draws Description, Paid promotion and "AI use, Tags" but
  leaves them out of the accessibility tree; the YouTube runner now finds them
  in a screenshot with `Windows.Media.Ocr` (offline, no model,
  `video_drop/ocr/`), taps the one exact match and proves the tap by the
  screen that opens. A label that is missing or appears twice stops the run.
  Multi-line descriptions are typed line by line and read back. New checklist
  row: **Screen text reader (Windows OCR)**.
- **Receipts read on their own.** In the hour after a final tap the app
  looks at the phone up to four times (3, 10, 25 and 55 minutes, read-only;
  a missed look is skipped, never caught up in a burst) and records the
  Instagram and Threads receipts the profile proves, so those destinations
  turn **posted** with no click. It stands down while another phone action,
  a link problem or a held phone lock is in the way. New setting: **Check
  each post in the apps afterwards** (on by default).
- **Instagram switches to the release's account by itself.** When Instagram
  is on another signed-in account, the runner opens Instagram's own account
  switcher, picks the exact handle (never a prefix match) and reads the
  profile header again before the Edits export; a switch that does not land
  stops the run with nothing posted.
- **USB pipe-stall recovery.** When WebDriverAgent and lockdown both stop
  answering while Windows still lists the phone, the link supervisor names it
  a pipe stall (`video_drop/pipe_stall.py`), skips the Home press and walks a
  ladder: wait, restart Apple Mobile Device Service, restart the iPhone's USB
  device node, each verified by lockdown answering again, and only then asks
  for a replug. Each step's outcome and seconds are logged.
- **USB recovery helper** for the two privileged ladder steps:
  `scripts\install_windows.ps1 -InstallUsbHelper` registers an on-demand
  SYSTEM scheduled task (one UAC prompt) that can only restart Apple Mobile
  Device Service or the iPhone's USB node; `-UninstallUsbHelper` removes it.
  The app itself never runs elevated. Design and threat model:
  [docs/usb-recovery-helper.md](docs/usb-recovery-helper.md).
- **Checklist rows:** USB recovery helper installed; iPhone USB path (warns
  about an AMD chipset controller or a hub between the phone and the PC);
  iPhone charging over USB (warns when the phone drains while plugged in or
  the battery is low).
- `scripts/set_passcode.py` saves `PHONE_PASSCODE` into `.env` from a hidden
  prompt and never prints it.

#### Fixed

- Screenshots fall back to WebDriverAgent's screenshot when go-ios's
  screenshot service on the phone stops answering, instead of stopping the
  run.
- A clip in a cloud folder reached through a short (8.3) or junction path is
  recognised as inside that folder.
- numpy is listed as a runtime dependency.
- Instagram's account check closes a story the app reopened on.
- **Receipts:** the Instagram receipt taps the Profile tab a second time
  (two seconds apart, never a double tap) so a profile reopened mid-scroll
  shows its post count again; the Threads receipt reads Threads' new "Threads
  tab" / "Replies tab" labels and accepts a caption whose hashtags Threads
  folded, only as that exact fold of the approved caption.
- **Focus:** a Focus menu left open by an earlier run is closed with a tap on
  empty space (Home leaves it up) before Control Center is read; Control
  Center is read at full depth again so the Focus module is found; an active
  Focus that already silences the phone (for example a Streaming Focus) is
  left untouched; the owner's Focus is remembered for 10 minutes so one Post
  now reads it once.
- **YouTube:** the audience picker is skipped when YouTube already remembers
  "not made for kids"; visibility is confirmed from the picker's own radio
  when the details row is stale; the share sheet gets up to 15 s to show the
  file size before it is judged.
- **Phone link:** the driver refuses an accessibility snapshot while a video
  app is visibly playing (the read that stalled the USB pipe) and the flows
  read pixels there instead: TikTok waits out its playing editor by pixels
  before reading the post screen, Instagram's cover proof and Edits' segment
  check read go-ios pixels, and leaving a playing app goes to the Home Screen
  through go-ios without asking WebDriverAgent; the supervisor retries its status-file replace
  when a reader holds the file open on Windows; the link experiment keeps
  recording through a phone error; a power warning is logged when the phone
  drains while reported as charging.
- Every runner checks the source file against the same database it loaded
  the release from.

#### Changed

- The test suite never reads the owner's real `.env` (`VIDEO_DROP_ENV_FILE`),
  and `pytest.ini` limits collection to `tests/`.

### 1.0.0-rc.2 (2026-09-30)

What was tried on the reference phone (iPhone 16 Pro Max, iOS 26.7) is marked
**live-tested**; everything else has offline tests only.

#### Added

- **Built-in phone driver.** The WebDriverAgent driver is now part of the app
  (`video_drop/phone/`, copied from SideTap under MIT with the provenance in
  `VENDORED.md`). No separate SideTap install, and no Codex-only storage
  location to find. `pymobiledevice3` (GPL-3.0) is used only for the re-sign
  step and only as a separate process.
- **Windows installer, complete.** `scripts/install_windows.ps1` installs the
  pinned packages, downloads go-ios 1.3.2 and the unsigned WebDriverAgent
  16.12.9 runner from their official GitHub releases with SHA-256 checks,
  repacks the runner as `wda\WebDriverAgent.ipa`, writes `GO_IOS_PATH` and
  `WDA_IPA` into `.env`, creates the Desktop shortcut and a Startup entry that
  brings the server and the phone link supervisor up at sign-in, and ends with
  the setup checklist. Idempotent; `-CheckOnly` reports without downloading or
  writing. Third-party license texts ship in `third_party/`.
- **Phone link supervisor** (live-tested): one background process owns the USB
  tunnel, the WebDriverAgent runner and the port forwards, probes them every
  five seconds and recovers a wedged runner with a Home press instead of a
  restart. Runners wait for it and declare busy windows around uploads.
  [docs/phone-link-reliability.md](docs/phone-link-reliability.md) has the
  root causes.
- **Instagram Post now through Edits** (live-tested): the clip goes OneDrive →
  Edits → 4K export → Instagram, with the first-frame cover proven by
  screenshot, the exact caption, and Instagram's own Facebook and Threads
  "Also share on" switches set from **Settings → Post now**. One upload covers
  three destinations; Threads is never posted separately unless you turn that
  on and the crosspost off.
- **Post now in order** (live-tested): YouTube, then Instagram (with its
  crossposts), then TikTok, each waiting for the one before.
- **Native Schedule runners for YouTube and Instagram** (built, partly
  live-tested): `scripts/phone_youtube_schedule.py` and
  `scripts/phone_instagram.py` enter the reserved slot in each app's own
  scheduler, reading every day button and time wheel back. Without `--commit`
  they stop before the final tap. The slot entry is proven from recordings;
  a committed schedule and its receipt are still open (see
  [docs/native-scheduling.md](docs/native-scheduling.md)).
- **Setup checklist rows:** Phone driver (replaces the SideTap row), Signed
  WebDriverAgent on the iPhone with the days left on the signature and the
  exact Sideloadly steps, Passcode saved for automation (`PHONE_PASSCODE` in
  `.env`, never read or shown by the checklist), Apple Mobile Device Service
  running, and USB power saving off. `python -m video_drop.setup_report`
  prints the same rows in a terminal.
- `scripts/phone_resign.py` re-signs WebDriverAgent after Sideloadly so taps
  work, and asks the supervisor to start the driver.
- Files-app route for Google Drive, Dropbox and iCloud Drive folders
  (`video_drop/files_app.py`, `source_route.py`): built, **not live-checked**.
- Native receipt read-back (`video_drop/receipts.py`,
  `scripts/phone_receipts.py`): in progress; manual **Posted** / **Scheduled**
  remain the working path.

#### Changed

- Posting times follow the PC's time zone (or the one chosen in Settings)
  instead of fixed US Eastern time.
- The Threads runner refuses Schedule-mode releases before touching the phone:
  native Threads scheduling is not built, so a scheduled release leaves
  Threads pending and says so.
- The release workflow runs the test suite on the tagged commit first and
  uploads nothing when it fails; the zip must carry the third-party notices
  and must not contain binaries or signing material.
- Tests and docs use placeholder accounts, clip names and device IDs only.
- The suite is run with pytest (`requirements-test.txt`), in CI and in the
  release build: `tests/conftest.py` pins the time zone and stubs the
  phone-link wait, which `unittest discover` does not load.

#### Known limitations

- Tried on one iPhone 16 Pro Max on iOS 26.7 only.
- Windows must keep the iPhone's USB port powered (the checklist's **USB power
  saving off** row) or the phone drops off the bus mid-upload.
- Native Threads scheduling is not built; the Files-app route and the native
  receipts are not live-checked; a committed YouTube or Instagram schedule
  has not been run on a throwaway clip yet.
- A free Apple ID's WebDriverAgent signature lasts 7 days.

### 1.0.0-rc.1 (2026-09-30)

#### Added

- First-run setup checklist in the editor: SideTap, iPhone over USB, phone
  link, phone details, free space, local AI (Ollama) and video folder, each
  with a plain-language fix. Every probe is read-only.
- TikTok posting from the iPhone app, with an account check and a first-frame
  cover check kept as a screenshot. **Post now** posts immediately; in
  **Schedule** mode the running app posts TikTok itself at the video's slot,
  at most once, within 15 minutes after it.
- **Posted** and **Scheduled** buttons to record that you checked a
  destination on the phone, and an unattended-streak counter in the header.
- Screen maps for YouTube, Instagram, Threads, TikTok and the iPhone Files app,
  built from recordings on the reference phone.
- YouTube taps that follow live controls and safe-area edges instead of
  proportions of one screen, with offline tests across iPhone SE to Pro Max
  sizes ([docs/size-independence-audit.md](docs/size-independence-audit.md)).
- Free-space check before every phone upload, and an option to remove the
  video from the iPhone after it posts.
- The release workflow scans each release zip with VirusTotal and keeps the
  README badge current.

#### Changed

- The phone link recovers without restarting a live WebDriverAgent session,
  and stops safely before the final tap if it cannot.
- The YouTube flow waits for the channel header and accepts each way iOS and
  OneDrive round a file size.
- Nested duplicate screen elements with an identical frame count as one
  target.

## 0.1.0 (2026-09-29)

First public source release: local editor and review queue, watch folder,
local-AI suggestions through Ollama, per-platform text confirmation, one to
five posting times a day with a choosable time, YouTube and Threads Post now
runners, phone-check settings, a Windows setup script with a Desktop shortcut,
`SECURITY.md`, and signed build provenance with a SHA-256 checksum for each
release zip.
