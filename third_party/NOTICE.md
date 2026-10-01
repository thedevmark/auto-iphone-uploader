# Third-party software

Auto iPhone Uploader is MIT licensed (see `LICENSE`). It ships with, or
downloads at install time, the following software from other projects. The
license texts in this folder are copied verbatim from the pinned upstream
versions and travel with every release zip.

| Component | Version | License | How it is used | Text |
| --- | --- | --- | --- | --- |
| [SideTap](https://github.com/ucsandman/SideTap) phone driver | upstream `0c75c53` | MIT, (c) 2026 Wes Sander | Vendored as `video_drop/phone/` (see `video_drop/phone/VENDORED.md` for what was copied and changed) | `LICENSE-SideTap` |
| [go-ios](https://github.com/danielpaulus/go-ios) | 1.3.2 | MIT, (c) 2019 danielpaulus | The USB connector to the iPhone. `scripts/install_windows.ps1` downloads the official Windows release zip, checks its SHA-256, and unpacks `ios.exe` into `tools/go-ios/`. The binary is never committed to this repository. | `LICENSE-go-ios` |
| [WebDriverAgent](https://github.com/appium/WebDriverAgent) | 16.12.9 | BSD-3-Clause, (c) 2015-present Facebook, Inc. | The on-phone input driver. The installer downloads the official **unsigned** `WebDriverAgentRunner-Runner.zip`, checks its SHA-256, and repacks it as `wda/WebDriverAgent.ipa`. You sign it with your own Apple ID (Sideloadly); a signed build is never redistributed by this project. | `LICENSE-WebDriverAgent` |
| [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) | 11.19.4 | GPL-3.0 | Not linked, not imported. `video_drop/phone/signing.py` runs it as a **separate process** to read the provisioning profile off the phone during the re-sign step. Installed by `pip` from `requirements-resign.txt` under its own license; it is not part of this app's code or release zip. The Windows installer (`AutoiPhoneUploader-Setup-<version>.exe`) carries the same unmodified, pip-installed copy, with its license files, in its own interpreter folder `python-resign\` apart from the app's `python\`; its source is the upstream tag `v11.19.4` and the PyPI sdist of that version. | upstream repository |
| [Python](https://www.python.org/) embeddable package | 3.14.8 | PSF License | The Windows installer carries the official embeddable build (pinned URL and SHA-256 in `scripts/build_installer.ps1`) so the PC needs no Python of its own; its `LICENSE.txt` is kept in the install folder. The packages from `requirements.txt` and `requirements-resign.txt` are installed into it unmodified, each with its own license files in its `.dist-info`. | `python\LICENSE.txt` |

Not shipped, not downloaded, never redistributed:

- **Sideloadly** is proprietary freeware. Install it yourself from
  https://sideloadly.io and sign WebDriverAgent with your own Apple ID.
- **Apple Devices / iTunes** supply the Apple Mobile Device Service (USB
  driver). Install from the Microsoft Store.
