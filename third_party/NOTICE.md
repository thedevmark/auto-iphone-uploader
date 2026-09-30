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
| [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) | 11.19.4 | GPL-3.0 | Not linked, not imported. `video_drop/phone/signing.py` runs it as a **separate process** to read the provisioning profile off the phone during the re-sign step. Installed by `pip` from `requirements-resign.txt` under its own license; it is not part of this app's code or release zip. | upstream repository |

Not shipped, not downloaded, never redistributed:

- **Sideloadly** is proprietary freeware. Install it yourself from
  https://sideloadly.io and sign WebDriverAgent with your own Apple ID.
- **Apple Devices / iTunes** supply the Apple Mobile Device Service (USB
  driver). Install from the Microsoft Store.
