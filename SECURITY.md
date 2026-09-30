# Security and trust

Auto iPhone Uploader drives social apps on a connected iPhone. That is a lot
to trust a download with, so this page says exactly what it does, what it
never does, and how to check the code you run is the code published here.

## What runs on your computer

- A local web server bound to `127.0.0.1:4748`. It is not reachable from
  other devices on your network, and it rejects requests whose `Host` or
  `Origin` is not that local address.
- A phone link supervisor (`video_drop/link_supervisor.py`), one background
  process that starts and watches the three go-ios processes the phone needs:
  the USB tunnel, the WebDriverAgent runner and the port forwards on
  `127.0.0.1:8100` and `127.0.0.1:9100`. It is the only thing that starts or
  stops them.
- The app's network calls at run time go only to programs on the same
  computer: the local Ollama model server (`127.0.0.1:11434` by default) and
  the phone link on `127.0.0.1:8100`. `tests/test_security_claims.py` fails
  the build if code gains any other address.
- The phone driver is part of this repository (`video_drop/phone/`, copied
  from [SideTap](https://github.com/ucsandman/SideTap), MIT; provenance and
  every change in `video_drop/phone/VENDORED.md`). Nothing is imported from a
  separate install at run time.
- Setup (`scripts/install_windows.ps1`) is the one step that reaches the
  internet, and it says so in its header:
  - `pip` installs the pinned packages from `requirements.txt` and, as a
    separate process-only tool, `pymobiledevice3` from
    `requirements-resign.txt`;
  - it downloads two files from their official GitHub releases and refuses
    them unless their SHA-256 matches the value pinned in the script:
    go-ios 1.3.2 (`ios.exe`, unpacked into `tools\go-ios\`) and the
    **unsigned** WebDriverAgent 16.12.9 runner (repacked as
    `wda\WebDriverAgent.ipa`). Neither is committed to this repository or
    shipped in a release zip;
  - it writes `GO_IOS_PATH` and `WDA_IPA` into `.env`, creates a Desktop
    shortcut and a Startup entry (server plus link supervisor, no browser
    window), and runs the read-only checklist. It installs no drivers or
    services and does not touch the phone. `-CheckOnly` downloads and writes
    nothing.
- The setup checklist only reads: it imports the driver in a child process,
  counts USB iPhones with `ios list` (it keeps the count, not the device ID),
  asks the phone link and Ollama whether they are running, reads the
  WebDriverAgent signing profile's expiry off the phone, checks whether
  `PHONE_PASSCODE` is set (never its value), queries the Apple Mobile Device
  Service and Windows USB power settings, and looks for OneDrive, Google
  Drive, Dropbox and iCloud Drive folders on this PC.
- Drafts, settings, the local database, the phone link's pid files and logs,
  cover screenshots and the re-sign material stay in the ignored `.state/`
  folder. Keep that folder private: go-ios logs there can name your device.

## What it does on your iPhone

- `phone_onboard.py` and **Check phone** are read-only: they record screen
  size, which social apps are installed, and which YouTube channels are
  signed in.
- Before a run the app can unlock the phone. That needs `PHONE_PASSCODE` in
  the app's `.env` (opt-in). The driver types it only when the lock-screen
  passcode pad is on screen, refuses to type any text that contains it
  anywhere else, and scrubs it from error messages. It is never logged,
  shown in the editor or sent anywhere.
- The runners open OneDrive to find the exact video, share it into the app,
  check the signed-in account matches the one you set, fill in the text you
  approved in the editor, and check the cover is the video's first frame
  before the final tap. **Post now** posts through the YouTube app, through
  Instagram via Meta's Edits app (with Instagram's own Facebook and Threads
  "Also share on" switches), and through the TikTok app. In **Schedule**
  mode the YouTube and Instagram runners enter your slot in each app's own
  scheduler; TikTok is posted by the app at the slot; Threads is left for you.
- A post is only submitted for text you confirmed, to the account you set.
  A final tap that times out is recorded as unconfirmed and never retried
  automatically.

## Signing WebDriverAgent

WebDriverAgent must be signed with your own Apple ID before the phone accepts
it. You do that in [Sideloadly](https://sideloadly.io), a separate program
this project does not bundle, download or automate; Apple's password and
2FA prompts appear in Sideloadly, never in this app. Sideloadly leaves the
runner's nested test bundle unsigned, so `scripts/phone_resign.py` then
re-signs `wda\WebDriverAgent.ipa` with go-ios. To do that it builds a signing
identity from the certificate Sideloadly created (`%APPDATA%\Sideloadly`,
into `.state\phone\wda.p12`) and reads the provisioning profile Apple minted
back off the phone with `pymobiledevice3`, run as a separate process. It
never sees your Apple ID password and makes no network call. A free Apple
ID's signature lasts 7 days; the checklist counts it down.

The app never stores or reads your Apple ID, device ID or platform passwords,
and it never uploads through a platform website.

## Verify a download

Each [release](https://github.com/thedevmark/auto-iphone-uploader/releases)
has a source zip built by GitHub Actions from the tagged commit, after the
test suite passed on that commit, a `SHA256SUMS.txt`, and a signed
build-provenance attestation. Check that your zip was built from this
repository:

```powershell
gh attestation verify auto-iphone-uploader-v1.0.0-rc.2.zip --repo thedevmark/auto-iphone-uploader
```

Or compare its hash with `SHA256SUMS.txt`:

```powershell
Get-FileHash auto-iphone-uploader-v1.0.0-rc.2.zip -Algorithm SHA256
```

The zip is plain Python, HTML and JSON with the third-party license texts in
`third_party/`; it contains no compiled binaries, so you can read it before
running it. The two binaries the installer fetches are verified against
SHA-256 values you can read in `scripts/install_windows.ps1` and compare with
the `digest` GitHub shows for those release assets. CodeQL scans every
change. When a `VT_API_KEY` secret is present, the release workflow also
scans the zip with VirusTotal and links the report from the release notes
and the README badge.

## Report a vulnerability

Use [private vulnerability reporting](https://github.com/thedevmark/auto-iphone-uploader/security/advisories/new)
rather than a public issue.
