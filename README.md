# Auto iPhone Uploader

<img src="web/logo.svg" width="64" height="64" alt="iPhone with an upload arrow">

[![Tests](https://github.com/thedevmark/auto-iphone-uploader/actions/workflows/tests.yml/badge.svg)](https://github.com/thedevmark/auto-iphone-uploader/actions/workflows/tests.yml)
[![CodeQL](https://github.com/thedevmark/auto-iphone-uploader/actions/workflows/codeql.yml/badge.svg)](https://github.com/thedevmark/auto-iphone-uploader/actions/workflows/codeql.yml)
[![VirusTotal v0.1.0: 0/65](https://img.shields.io/badge/VirusTotal%20v0.1.0-0%2F65%20flagged-brightgreen)](https://www.virustotal.com/gui/file/c4ae924bcd67620b0248bd3a628f42fc9e7e28496058e6066dfd3640743b8f6b)

Finish a video on your Windows PC, write its title and captions once, and
post it from the real apps on your iPhone. Native iPhone uploads keep the
quality that platform websites and browser uploaders throw away; the
[4K60 Native Ingest paper](https://github.com/thedevmark/engineering-notes/blob/main/4k60-native-ingest/README.md)
has the tests behind that.

**Before you install:** this app taps through social apps on your iPhone.
[SECURITY.md](SECURITY.md) lists exactly what it does and never does on your
phone and computer, and how to check a download was built from this
repository by GitHub.

## Status: early preview

It works today for one operator's setup. Expect rough edges.

| | Works now | Not yet |
|---|---|---|
| Editor | Import a finished video, get suggested text from a local model, edit and confirm the exact title, caption, and hashtags per platform | |
| Threads | Post now from the iPhone app | |
| YouTube Shorts | Fill the iPhone composer for review; Post now is built but not yet proven on a live phone | Scheduling |
| Instagram, TikTok, Facebook | Text is prepared for review | Posting from the phone |

Nothing posts without you confirming its exact text and choosing **Post now**.

## What you need

- Windows 10 or 11 with [Python 3.11+](https://www.python.org/downloads/)
- [ffmpeg](https://ffmpeg.org/download.html) (`ffmpeg` and `ffprobe` on PATH);
  every import is fully decoded before it is accepted
- An iPhone on USB, set up with
  [SideTap](https://github.com/ucsandman/SideTap/blob/main/docs/setup-windows.md).
  SideTap is a separate project that does the tapping; install and review it
  yourself.
- OneDrive syncing your finished videos to the phone; the phone apps pick the
  file from there
- Optional: [Ollama](https://ollama.com) with `qwen2.5vl:7b` and `qwen3:14b`
  for suggested titles and captions. Without it you write the text yourself.

## Install

1. Download the zip from the latest
   [release](https://github.com/thedevmark/auto-iphone-uploader/releases) and
   optionally [verify it](SECURITY.md#verify-a-download).
2. Unzip it, open a terminal in that folder, and install the two pinned
   packages (Pillow and timezone data):

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Start the app. It opens the editor at `http://127.0.0.1:4748`, which only
   this computer can reach:

   ```powershell
   python launch_video_drop.py
   ```

Want a Desktop shortcut? `scripts/install_windows.ps1` installs the same
packages and adds one. Windows blocks downloaded scripts by default, so
read the script first, then run it with
`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1`.
The bypass applies to that one run only.

## First run

1. Put the account each platform should post to in `.state/accounts.json`,
   for example `{"youtube": "@your-channel", "threads": "@you"}`. Every phone
   run stops if the signed-in account does not match.
2. Pick a finished video from your OneDrive `_Videos` folder (or set
   `VIDEO_DROP_SOURCE_DIR`), or turn on **Watch folder** in Settings to pick up
   new exports automatically.
3. Edit the text, confirm it, choose **Post now**, and press the post button.
   Scheduling is not connected yet.

[docs/how-it-works.md](docs/how-it-works.md) covers the details: the watch
folder, text rules per platform, the slot planner, receipts, and the phone
scripts.

## Develop

```powershell
python -m unittest discover -s tests -v
```

GitHub Actions runs the suite on Windows and Linux. Live iPhone behavior is
checked separately because CI has no phone or platform accounts. This repo is
also taking over the video workflow from the author's Homebase system; see
[MIGRATION.md](MIGRATION.md).

MIT licensed; see [LICENSE](LICENSE).
