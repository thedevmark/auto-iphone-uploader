# Changelog

All notable changes to Auto iPhone Uploader. Versions match the Git tags that
[release.yml](.github/workflows/release.yml) builds release zips from.

## 1.0.0 (unreleased; current candidate 1.0.0-rc.1)

The first release meant for people other than its author. See the README's
[Known limitations](README.md#known-limitations) before relying on it.

### Added

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

### Changed

- The phone link recovers without restarting a live WebDriverAgent session,
  and stops safely before the final tap if it cannot.
- The YouTube flow waits for the channel header and accepts each way iOS and
  OneDrive round a file size.
- Nested duplicate screen elements with an identical frame count as one
  target.
- Tests and docs use placeholder accounts and clip names only.

### Known limitations

- Tried on one iPhone 16 Pro Max on iOS 26.7 only.
- YouTube, Instagram and Facebook scheduling is done by hand in the phone apps;
  Instagram and Facebook posting is done by hand.
- The editor has no control yet to put a video on a posting time; the planner
  is reachable through `POST /api/queue/plan`.
- Phone runners open videos from OneDrive only.
- Posting times use US Eastern time.

## 0.1.0 (2026-09-29)

First public source release: local editor and review queue, watch folder,
local-AI suggestions through Ollama, per-platform text confirmation, one to
five posting times a day with a choosable time, YouTube and Threads Post now
runners, phone-check settings, a Windows setup script with a Desktop shortcut,
`SECURITY.md`, and signed build provenance with a SHA-256 checksum for each
release zip.
