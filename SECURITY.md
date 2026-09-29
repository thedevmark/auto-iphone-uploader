# Security and trust

Auto iPhone Uploader drives social apps on a connected iPhone. That is a lot
to trust a download with, so this page says exactly what it does, what it
never does, and how to check the code you run is the code published here.

## What runs on your computer

- A local web server bound to `127.0.0.1:4748`. It is not reachable from
  other devices on your network, and it rejects requests whose `Host` or
  `Origin` is not that local address.
- Its only network calls go to programs on the same computer: the local
  Ollama model server (`127.0.0.1:11434` by default) and the SideTap viewer
  (`127.0.0.1:8770`). `tests/test_security_claims.py` fails the build if code
  gains any other address.
- Setup (`scripts/install_windows.ps1`) installs the pinned packages in
  `requirements.txt` from PyPI and creates one Desktop shortcut. It installs
  no drivers or services and does not touch the phone.
- Drafts, settings, and phone profiles stay in the ignored `.state/` folder.

## What it does on your iPhone

Phone control goes through [SideTap](https://github.com/ucsandman/SideTap), a
separate open-source project you install yourself. This repository does not
bundle, modify, or download SideTap.

- `phone_onboard.py` is read-only: it records screen size, which social apps
  are installed, and which YouTube channels are signed in.
- The YouTube, Threads, and Instagram runners open that app, check the
  signed-in account matches the one you set, and fill in the text you
  approved in the editor.
- A post is only submitted when you chose **Post now** for that video and
  confirmed its exact text. A final tap that times out is never retried
  automatically.

It never stores or reads your Apple ID, passcode, device ID, or platform
passwords, and it never uploads through a platform website.

## Verify a download

Each [release](https://github.com/thedevmark/auto-iphone-uploader/releases)
has a source zip built by GitHub Actions from the tagged commit, a
`SHA256SUMS.txt`, and a signed build-provenance attestation. Check that your
zip was built from this repository:

```powershell
gh attestation verify auto-iphone-uploader-v0.1.0.zip --repo thedevmark/auto-iphone-uploader
```

Or compare its hash with `SHA256SUMS.txt`:

```powershell
Get-FileHash auto-iphone-uploader-v0.1.0.zip -Algorithm SHA256
```

Once a VirusTotal key is configured, release notes also link a scan of the
same zip. The source is plain Python and HTML with no compiled binaries, so
you can also read it before running it. CodeQL scans every change for security issues.

## Report a vulnerability

Use [private vulnerability reporting](https://github.com/thedevmark/auto-iphone-uploader/security/advisories/new)
rather than a public issue.
