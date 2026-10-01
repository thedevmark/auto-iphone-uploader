# Set up Auto iPhone Uploader on Windows

About fifteen minutes, three things to do: one command, one signing, one
passcode. Everything else the app checks and fixes for you.

You need a Windows 11 PC, an iPhone with a USB cable, your Apple ID, and
Python 3.11 or newer ([python.org](https://www.python.org/downloads/), tick
"Add python.exe to PATH" in its installer).

## 1. One command

Download the app (the release zip, or `git clone`), open PowerShell in that
folder, and paste:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

It installs the app's Python packages, downloads the iPhone connector and the
phone control app (both pinned by version and checked by SHA-256 before anything
is unpacked), writes the paths into `.env`, puts a shortcut on the Desktop and
a start-at-sign-in entry in Startup, then opens the app. Nothing is installed
system-wide. Along the way it asks two yes/no questions:

- **Save your iPhone passcode now?** The app unlocks the iPhone before each
  post. You type the passcode at a hidden prompt; it is saved in `.env`,
  typed only on the lock screen, and never shown or logged. (Later:
  `python scripts\set_passcode.py`.)
- **Install the USB recovery helper now?** Recommended. Windows asks for
  administrator rights once. When the iPhone's USB connection stalls
  mid-upload, the helper restarts Apple's driver and resets the port by
  itself instead of asking you to replug. (Later: the same command with
  `-InstallUsbHelper`; details in [usb-recovery-helper.md](usb-recovery-helper.md).)

Run the command again any time: finished steps are skipped. `-CheckOnly`
reports without changing anything; `-NoPrompt` skips the questions;
`-NoLaunch` leaves the app closed.

## 2. The app's first-run screens

The app opens at `http://127.0.0.1:4748` with four short screens. Each shows
one thing to do at a time, in one sentence, and moves on by itself when the
check turns green; the technical detail and any command to paste sit behind
"Show me how". Checks run every few seconds and never tap the iPhone. The
header's theme control (Auto, Light, Dark) follows your system by default.

1. **Connect your iPhone.** Plug it into a port on the PC itself (not a hub or
   a front-panel port), unlock it, tap Trust. If Windows has never seen an
   iPhone, install *Apple Devices* from the Microsoft Store.
2. **Let your PC control it.** The one step Apple does not let a script do:
   sign the phone control app with your own Apple ID.
   1. Install [Sideloadly](https://sideloadly.io).
   2. Drag `wda\WebDriverAgent.ipa` from the app folder onto Sideloadly, pick
      the iPhone, type your Apple ID and click Start. Apple asks for your
      password and a code inside Sideloadly, never in this app.
   3. On the iPhone: Settings › General › VPN & Device Management › your
      Apple ID › Trust.
   4. In the app folder run `python scripts\phone_resign.py`. It finishes the
      signing so taps work and starts the connection.

   A free Apple ID's signing lasts seven days; the checklist counts the days
   down and the same script renews it.
3. **Pick your video folder.** A folder inside OneDrive, Google Drive,
   Dropbox or iCloud Drive, so the iPhone can open what your editor exports.
4. **Check your apps.** One read of the iPhone's apps and your YouTube channel.
   Then you're ready: every finished export in the folder becomes a draft here.

## Afterwards

The full checklist stays in **Settings › Open setup checklist**, with a fix for
every row, and **Guided setup** reopens the four screens. Its **Extras** are
optional and say in one line what each adds: the USB recovery helper (a stalled
connection fixes itself), a direct USB port and charging (fewer drops during
long uploads), free space on the iPhone, and local AI through
[Ollama](https://ollama.com), which drafts titles and captions from each video
(`winget install Ollama.Ollama`, then `ollama pull qwen2.5vl:7b` and
`ollama pull qwen3:14b`); without it you write them yourself. The app starts at
sign-in and keeps the iPhone connection up by itself; double-click the Desktop
shortcut to open the editor.

## If something fails

Every failure line in the installer and every red row in the app ends with
what to do next. The three that come up:

| What you see | What to do |
| --- | --- |
| *The downloaded … is not the file this app expects* | A proxy or a captive network changed the download. Nothing was installed. Reconnect and run the command again. |
| *Windows cannot see iPhones yet* | Install *Apple Devices* from the Microsoft Store, replug the iPhone, unlock it, tap Trust. |
| *The iPhone is connected, but it is not answering taps yet* | Unlock the iPhone and tap Trust if it asks. If it stays that way, unplug and replug it; if the signing expired, run `python scripts\phone_resign.py`. |
