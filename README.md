# Auto iPhone Uploader

<img src="web/logo.svg" width="64" height="64" alt="iPhone with an upload arrow">

Licensed under MIT; see [LICENSE](LICENSE). The connected iPhone, platform
accounts, SideTap installation, and local media are external to this repository.

This is a standalone repository for the finished-video workflow. **Migration
is still in progress. Homebase remains the live publishing system.** The local
app below can import a video and let Mark edit and confirm the final public text. Its slot
planner is internal until native platform scheduling is connected. The app
does not yet perform an unattended platform upload.
All social-platform uploads, schedules, and receipt checks must run through the
connected iPhone with SideTap. The local browser page is only an editor; no
platform website is an upload fallback.

The [4K60 Native Ingest engineering paper](https://github.com/thedevmark/engineering-notes/blob/main/4k60-native-ingest/README.md) explains the findings from 40+ videos testing codecs, bitrates, resolutions, a Chrome upload extension, and native iPhone uploads that informed this phone-first workflow.

## Run the local app

On Windows with Python 3.11+, clone this repository and run its local setup:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
```

This installs Pillow and timezone data into the selected Python environment
and creates an **Auto iPhone Uploader** Desktop shortcut. It
preserves an existing shortcut pointed at another checkout; use
`-ReplaceShortcut` only when you want to change its target. It does not install
phone drivers, sign into accounts, or touch the iPhone. Follow
[SideTap's Windows setup](https://github.com/ucsandman/SideTap/blob/main/docs/setup-windows.md)
before using native phone uploads.

Double-click the shortcut, or run `pythonw launch_video_drop.py`. The launcher starts the local server
in the background if needed and opens the editor. Closing the editor window
does not stop the server; run the launcher again to reopen it. If the source
changes while the server is running, the launcher shows a clear stale-server
message instead of opening the old version. Restart that server before opening
the updated app; local draft data remains in `.state`.

The core app uses only the Python standard library.
For local transcription, install `requirements-analysis.txt` and put
`ffmpeg` and `ffprobe` on PATH. Ollama must serve locally on port 11434 with
the configured vision and text models already downloaded; by default these
are `qwen2.5vl:7b` and `qwen3:14b`. The smaller vision model was verified
against a frame of the Valheim test clip; it identified the visible game while
the former Gemma 4 default incorrectly said no image was supplied. The 14B text
model completed a full local Valheim pass in about 30 seconds on the operator's
16 GB GPU; the former 27B default timed out in background analysis. Model names
can be changed with
`VIDEO_DROP_VISION_MODEL` and `VIDEO_DROP_TEXT_MODEL`. Failed capabilities
produce a partial analysis rather than an authorized post. If the local model
server is stopped while drafts are analyzed, Auto iPhone Uploader waits for both models
to appear in Ollama and retries those drafts when the service returns. A
transient local-model failure is retried with a delay, up to three attempts.
Typed text remains in the editor during the retry.

```powershell
python -m video_drop.server
```

Open `http://127.0.0.1:4748` and choose a finished video from the searchable
OneDrive `_Videos` list, enter another exact path, or turn on Watch folder in
Settings. When watch is enabled,
existing files are skipped; new `.mp4`, `.mov`, `.m4v`, and `.webm` exports enter
the review queue only after their size and modification time stay unchanged for
30 seconds, the writer releases its Windows file handle, and ffprobe plus a
full ffmpeg video/audio decode succeed. An incomplete file is retried; a temp
extension and Premiere's numbered `.m4v` intermediate are ignored. Auto iPhone Uploader reads the original in place and does not copy
it. The local database is
`.state/video-drop.sqlite`, which is ignored by Git. Scripts can use
`POST /api/import-path` with an exact local path for the same in-place import.
Both manual import routes require the finished file to pass a full video and
audio decode before creating a draft.
The editor plays the original video in place and refuses the preview if the
source file no longer matches the imported hash.
The local server must run while new exports are detected; a folder that was
already enabled keeps its state across restarts and catches new files created
while the server was closed.

Intended platform accounts live in ignored `.state/accounts.json` as a JSON
object keyed by platform (for example, `{"youtube":"@your-channel"}`).
Phone preparation checks that the release's confirmed account matches this
local target; a missing or changed target stops the run before opening media.
New drafts inherit these local targets. Each phone composer still rechecks its
selected account before a submit; the local file is an intended target, not
proof of the current login.

The slot planner defaults to successive 10 AM and 7 PM New York times. Settings
can hold one to five distinct daily times. With 10 AM and 7 PM selected, four
videos planned before 10 AM take two days. The internal batch reservation holds
the selected clips in queue order in one database transaction; if any clip fails
validation, none of their times change. Reserving a
slot does not mean a platform accepted it. An expired, unattempted reservation
moves to the next free time; an expired time with a possible phone submission
requires a native receipt check before any retry. The desktop Schedule action is
disabled until the native-app batch can submit and verify each platform's
schedule. The local `POST /api/queue/plan` route accepts
`{"releaseIds":[1,2]}` for confirmed clips and returns their reserved times
with `nativeScheduled: false`; it does not send either clip to the phone.
Each new video defaults to Schedule; Post now is an explicit saved
choice that takes no time slot. Threads and YouTube have one-shot native Post now
actions; YouTube requires choosing Post now first. Both stay unconfirmed after
the final tap until their native receipts are checked. Other destinations remain
disabled until their phone runners and receipts are connected. Multiple drafts can be imported
and analyzed while the editor shows one selected video at a time. Editing text
after authorization clears its authorization. The editor accepts one title and
one shared set of hashtags for the selected clip. Applying them builds the
reviewable platform text: YouTube's title and description end in `#shorts`;
Instagram and Facebook captions end in `#reels`; TikTok's caption ends in
`#fyp`; Threads uses the shared hashtags without a platform suffix. YouTube's
description prose and comma-separated tags stay editable. Facebook and Threads
retain their own account identities. Any changed output must be reviewed and
confirmed again. The Threads tab can start an immediate native phone post once
its exact caption is confirmed. A final tap remains unconfirmed until a matching
post is checked, and Auto iPhone Uploader never retries it automatically.

An operator who has checked a matching item in a native app's Scheduled
content list can record that observed schedule with
`Store.record_observed_schedule`. It checks the saved account, caption, time,
and approved revision, and stores a hash of the local screenshot. This is a
manual observation; it does not enable unattended Instagram scheduling or
prove that a future post was published.

```powershell
python -m unittest discover -s tests -v
```

The phone scripts and the cross-platform test suite need Pillow. Install
`requirements.txt` for a reproducible local environment; it also includes
`tzdata` for Windows timezone support. GitHub Actions runs the suite
on Windows and Linux. Live iPhone behavior is checked separately because CI
has no connected device or platform accounts.

## Homebase data snapshot

The importer opens Homebase's SQLite database read-only. Without `--apply` it
prints counts and migration hazards. With `--apply` it creates a **new**
destination database and refuses to overwrite one. Existing statuses and all
legacy release, upload, public-text revision, and observation fields are
retained as JSON for the later cutover; legacy authorization is not reused for
new posts.

```powershell
python scripts/import_homebase.py --source <path-to-Homebase-database> --dest .state\homebase-snapshot.sqlite
python scripts/import_homebase.py --source <path-to-Homebase-database> --dest .state\homebase-snapshot.sqlite --apply
python scripts/import_homebase.py --source <path-to-Homebase-database> --dest .state\homebase-snapshot.sqlite --compare
python scripts/import_homebase.py --source <path-to-Homebase-database> --existing .state\video-drop.sqlite --dest .state\merged-preview.sqlite --merge-copy
```

`--merge-copy` uses SQLite's online backup to stage a standalone database copy,
then adds Homebase history and verifies the result before creating the requested
file. Failed merges leave no candidate file. It rejects duplicate media and an
already imported Homebase history. Both source databases remain untouched;
the new copy is a cutover candidate, not an automatic switch.

The SideTap YouTube preparation runner is in `scripts/phone_youtube.py`.
Pass an Auto iPhone Uploader release ID; it reads the confirmed text and original file
identity from the local database, checks the OneDrive share sheet and YouTube
channel, and fills the composer. An older manifest path also works only when
every field matches that confirmed release. Preparation stops before Upload
Short. A confirmed Post now release can use the one-shot Upload Short action;
the app records an unconfirmed attempt before that tap and requires a native
receipt check. Native scheduling remains disabled until the app can enter and
read back a matching platform schedule. The YouTube Post now path has unit and
HTTP coverage but has not yet passed a live connected-phone run.

For a connected phone, `python scripts/phone_onboard.py` records screen size,
installed social apps, and the currently selected/available YouTube channels
in a local `.state/phone-profiles/` file. It does not store Apple credentials,
the device ID, or the phone's unlock code. An installed app is marked unverified
until its own account screen has been inspected. The YouTube runner selects the
expected signed-in channel before opening the file, verifies it again in the
composer, and scales its few unlabeled controls from the measured portrait
screen size. Other app account screens still need mapping before phone
automation can use them.

The phone runner requires a local SideTap installation and Pillow. It cannot
recover from a USB device that disappears from Windows. The existing runbook
in `docs/phone-social-runbook.md` records what was observed and what remains
to map. The scheduling and metadata modules are being moved next.

See `MIGRATION.md` for the cutover and removal gates.
