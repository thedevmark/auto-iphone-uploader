# 1.0 launch checklist

Every gate that still stands between today and `v1.0.0`, with its status and
the exact step that proves it. Statuses: **done**, **live-tested** (worked on
the reference phone at least once), **built, partly live-tested**, **built,
not live-tested**, **in progress**, **not built**. The bars are the ones in
[goal-v1-public.md](goal-v1-public.md); the reference setup is an iPhone 16 Pro
Max on iOS 26.7 over USB to a Windows 11 PC. Update the status column in the
same commit that changes the fact.

## Product bars

| # | Gate | Status | Verify |
|---|------|--------|--------|
| 1 | **Unattended streak: 20 Schedule-mode releases in a row, no hand fix** | not started (streak 0) | The header's **Unattended streak** reads 20 / 20. Count only releases after receipts are read automatically; `python -m pytest tests/test_receipts_and_streak.py -q` pins the rule that a manual receipt ends the streak. |
| 2a | **Post now posts everything immediately** | live-tested: YouTube, Instagram via Edits 4K with the Facebook + Threads crossposts, TikTok (reference release release, 2026-10-01, all five platforms up). YouTube's Description / Paid promotion / AI use rows are hidden from accessibility since YouTube 21.38 and are now opened by offline Windows OCR; the full details screen was reached hands-free in a dry run on 2026-10-01 | On a confirmed Post now release: `python scripts\phone_youtube.py <id> --commit`, `python scripts\phone_instagram.py <id> --commit`, `python scripts\phone_tiktok.py <id> --commit` (or the editor's per-platform post buttons). Each ends with the post visible in the app and the destination confirmed. |
| 2b | **Schedule enters the slot in YouTube's own scheduler** | built, partly live-tested (slot entry proven in recordings; `--commit` not yet run on a throwaway clip) | `python scripts\phone_youtube_schedule.py <id>` stops with the date and time read back from the picker. Then once with `--commit` on a throwaway clip; the Short appears under the channel's scheduled videos at the slot. Live checks still open are listed in [native-scheduling.md](native-scheduling.md). |
| 2c | **Schedule enters the slot in Instagram's own scheduler (Facebook rides the crosspost)** | built, partly live-tested (Date row proven; the time popover was never recorded) | `python scripts\phone_instagram.py <id>` on a Schedule release stops before **Schedule** with date and time read back and "1 profile", Facebook on, Threads off. Record the time popover with `python scripts\record_screen.py` if it stops at "time picker is not mapped yet". Then one `--commit` on a throwaway clip; Instagram's Scheduled content shows the reel at the slot. |
| 2d | **TikTok in Schedule mode is left to the owner (no native scheduler on this account)** | built (owner's choice, 2026-10-04): the app never posts TikTok at the slot | Plan a release in Schedule mode: TikTok shows **Needs you** with "post it yourself, or press Post now on TikTok", nothing posts it at the slot, and Post now on TikTok works before the slot (`tests.test_slot_posts` OwnerPostsTikTokTests). |
| 2e | **Threads scheduled natively in the Threads app** | not built (Post now covers Threads through Instagram's crosspost; `scripts\phone_threads.py` refuses scheduled releases) | Build the Photos → Threads composer → Schedule route, record its screens, then a `--commit` on a throwaway clip and a native scheduled-item receipt. Until then a Schedule release leaves Threads pending and says so. |
| 3 | **Order and crosspost: YouTube → Instagram (+Facebook, Threads) → TikTok → rest** | live-tested (Post now) | `python -m pytest tests/test_post_now_routing.py tests/test_crossposts.py -q`; one live Post now release with **Post in order** on shows the three apps posting in that order and Instagram's receipt naming Facebook and Threads. |
| 4 | **Cover gate: first frame confirmed by screenshot before the final tap** | live-tested on YouTube, Instagram (Edits) and TikTok; Facebook and Threads inherit Instagram's cover | Each run keeps the cover screenshot under `.state/` and names it in the destination's evidence; a run whose cover cannot be confirmed must stop before the final tap (`tests.test_youtube_post_now`, `tests.test_phone_instagram`, `tests.test_phone_tiktok`). |
| 5 | **Receipts read back natively; nothing left "unconfirmed" after an hour** | in progress: Instagram Post now (post count + first-frame newest tile) and Threads crosspost (newest caption, folded hashtags accepted only as the exact fold) verified live on reference release, 2026-10-01; Facebook, TikTok and YouTube receipts still need recordings; the Instagram and Threads reads run on their own at 3, 10, 25 and 55 minutes after the final tap (`video_drop/receipt_sweep.py`, the **Check each post in the apps afterwards** setting) | After a Post now release, `GET /api/releases/<id>` shows every destination `posted` with a receipt source other than `manual`, within an hour, with no click in the editor. Instagram's scheduled receipt: `instagram_schedule.verified_scheduled_reel` against a recorded Scheduled content screen. YouTube's scheduled list still needs recording. |
| 6 | **Link survives: tunnel or WDA drops recover on their own** | live-tested for wedges and USB drops (supervisor since 2026-09-30). On the AMD chipset USB controller (DEV_43D5) a video-screen accessibility read stalled the whole USB pipe; on the CPU-attached controller (DEV_149C) 20 min of the same load and a 9-min pixels arm on 2026-10-01 had 0 stalls (AX arm running); the pipe-stall ladder (AMDS restart, USB node restart through the SYSTEM helper, then replug) is built and unit-tested, not yet seen on a real stall | `python scripts\link_soak.py --minutes 30` (read-only) reports no `needs-replug`; `.state/link-events.jsonl` shows wedges cleared by Home presses, not restarts, and any `pipe-stall` ending in a `ladder` record with `recovered: true`. Helper installed: the checklist row **USB recovery helper installed** is green. `python -m pytest tests/test_link_supervisor.py tests/test_pipe_stall.py tests/test_usb_helper.py tests/test_phone_link.py -q`. |
| 7 | **Any phone: calibration run builds the maps for a new iPhone** | not built (maps recorded on the reference phone; YouTube taps proven on SE to Pro Max sizes by `tests.test_size_independence` only) | A second iPhone of another size completes **Check phone**, a calibration run and one Post now release without a map edit. |
| 8 | **Any cloud: OneDrive, Google Drive, Dropbox, iCloud Drive via Files → Share** | in progress (`video_drop/files_app.py`, `source_route.py`, `maps/files` landing; OneDrive app route is the proven one) | One live Post now release from each provider's folder, watched by the app, with the Files route (`filesAppForOneDrive` on for OneDrive). Until then the checklist says which providers post. |
| 9 | **Fifteen-minute setup on a fresh PC** | built, not live-tested end to end (installer, pins, checklist rows and the re-sign script are new today) | On a clean Windows VM with a real phone, someone other than the author runs `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_windows.ps1`, signs WDA in Sideloadly, runs `python scripts\phone_resign.py`, and reaches a green checklist and a first scheduled post inside 15 minutes. Timed. |
| 10 | **Public: repo, README, license, install guide, demo video, release on GitHub Releases** | in progress (repo, README, SECURITY, CHANGELOG, release workflow exist; no `v1.0.0` tag, no demo video, no landing page) | `v1.0.0` tag exists; the release has the zip, `SHA256SUMS.txt`, the attestation and `third_party/` inside the zip; `gh attestation verify` passes; the 60-second demo is linked from the README. |

## Packaging gates (this week's work)

| Gate | Status | Verify |
|------|--------|--------|
| go-ios 1.3.2 pinned by SHA-256, unpacked into the app folder, never committed | built; hash taken from GitHub's release-asset digest, download path not yet exercised | Run the installer without `-SkipDownloads` on a machine with network: it prints `go-ios-win.zip verified (SHA-256 939C6B…)` and `tools\go-ios\ios.exe --version` prints 1.3.2. `git ls-files tools wda` prints nothing. |
| WebDriverAgent 16.12.9 unsigned runner pinned by SHA-256, repacked as `wda\WebDriverAgent.ipa` | built; repack proven on the previous runner zip (same layout as the working SideTap ipa minus dSYM), pinned download not yet exercised | Installer prints `WebDriverAgentRunner-Runner.zip verified (SHA-256 8A48EC…)`; Sideloadly accepts `wda\WebDriverAgent.ipa`; `python scripts\phone_resign.py` ends with "Input is live". `python -m pytest tests/test_wda_ipa.py -q`. |
| Third-party notices travel with the release | done (`third_party/NOTICE.md` + license texts; release zip includes them) | Unzip a release and open `third_party/NOTICE.md`; `tools\go-ios\LICENSE` and `wda\LICENSE` exist after the installer runs. |
| pymobiledevice3 (GPL-3.0) never imported into the app | done (child interpreter only) | `grep -rn "import pymobiledevice3\|from pymobiledevice3" video_drop scripts` finds only the string inside `_MISAGENT_SCRIPT`. `tests.test_phone_driver_signing` passes. |
| `.env` holds `GO_IOS_PATH`, `WDA_IPA`, `PHONE_PASSCODE`; the passcode is never read by setup or printed | done | `scripts\install_windows.ps1 -CheckOnly` prints "PHONE_PASSCODE is set in .env (never shown)" and nothing else about it; `tests.test_setup_check` (`PasscodeTests`) passes. |
| Startup entry brings the server and the link supervisor up at sign-in | built, not live-tested across a reboot | After a sign-in, `http://127.0.0.1:4748/api/link` answers and `.state/link-supervisor.pid` names a live process, with no browser window opened. |
| Setup checklist: signed-WDA days left, passcode saved, Apple Mobile Device Service | built; the days-left read needs a connected phone to be seen live | With the phone plugged in, **Settings → Open setup checklist** shows "Signed · N days left (until …)"; stop the service (`net stop "Apple Mobile Device Service"`, admin) and **Check again** turns that row red with the services.msc fix. |
| Installer `-CheckOnly` in CI | done (`tests.yml` runs it on Windows with `-NoChecklist`) | The Tests workflow's Windows job is green. |

## Release mechanics

| Gate | Status | Verify |
|------|--------|--------|
| Private and public test suites green | done at port time (see CHANGELOG for the counts) | `python -m pytest tests -q` in both checkouts. |
| The suite runs under pytest only | done (both CIs run pytest; `requirements-test.txt` pins it) | `tests/conftest.py` pins the PC time zone and stubs `server.wait_for_link`; `python -m unittest discover` skips it and 28 phone-run tests then wait on a real phone link and fail. Do not switch CI back to unittest without moving those stubs into the test modules. |
| Privacy sweep of the public tree | done at port time | From the public checkout: `git grep -n -i -E "deutschmark|00008[0-9]{3}-[0-9A-F]{16}|Users\\\\mark|Users/mark|D:\\\\OneDrive|terry|day5td|61594285786303" -- . ":!CHANGELOG.md" ":!LICENSE" ":!docs/launch-checklist.md"` prints nothing (the LICENSE holder line is the author's own). |
| Release build runs the tests first and fails when they fail | done (`release.yml` `tests` job gates `source`) | A release published from a commit with a failing test uploads nothing. |
| VirusTotal scan | optional (runs only with a `VT_API_KEY` secret) | The release notes gain a VirusTotal link when the secret exists. |
| Tag `v1.0.0` | not done (current candidate `1.0.0-rc.4`) | `gh release view v1.0.0` lists the zip, `SHA256SUMS.txt` and the attestation. |
| 60-second demo video | not done | Linked from the README's first screen. |
| Three outside testers | not done | Three issues or messages from people who reached a first scheduled post on their own phone, with the setup time. |

## Known limits that ship with 1.0 (say them, do not hide them)

- Tested on one phone: iPhone 16 Pro Max, iOS 26.7. Other sizes are covered by
  tests for YouTube only.
- Windows must keep the iPhone's USB port powered (the checklist's **USB power
  saving off** row); otherwise the phone drops mid-upload.
- A free Apple ID's WebDriverAgent signature lasts 7 days; the checklist counts
  it down and `scripts\phone_resign.py` renews it.
- English app labels only. YouTube's hidden details rows are read with Windows' built-in OCR
  engine (Windows.Media.Ocr, offline), which needs an English OCR language pack (installed
  with English Windows).
- Plug the iPhone into a USB port on the CPU's own controller (usually the rear I/O
  ports nearest the CPU), not an AMD chipset port; the checklist's **iPhone USB path**
  row warns about the chipset controller.
- TikTok is never posted at the slot: in Schedule mode, post it yourself or press Post now on TikTok.
