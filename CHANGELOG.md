# Changelog

All notable changes to Auto iPhone Uploader. Versions match the Git tags that
[release.yml](.github/workflows/release.yml) builds release zips from.

## 1.0.0 (unreleased; current candidate 1.0.0-rc.2)

The first release meant for people other than its author. See the README's
[Known limitations](README.md#known-limitations) and
[docs/launch-checklist.md](docs/launch-checklist.md) for what still stands
between this candidate and 1.0.

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
