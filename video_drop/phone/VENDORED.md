# Vendored phone driver (from SideTap)

`video_drop/phone/` is a pinned, trimmed copy of the WebDriverAgent driver from
**SideTap** by Wes Sander, MIT licensed (`LICENSE-SideTap`, © 2026 Wes Sander).
Phase 1 of the cutover: the app no longer imports the separately installed
SideTap at runtime. Nothing here is pip-installable from upstream; updating
means re-copying files and re-reading this note.

## What was copied, and from where

| Vendored file | Upstream file (`src/phone_harness/`) | How much |
| --- | --- | --- |
| `wda_client.py` | `wda_client.py` | whole; two recovery hints reworded (below) |
| `capture.py` | `capture.py` | whole; the go-ios-missing message comes from `device` |
| `device.py` | `device.py` **working tree** | trimmed (below) |
| `helpers.py` | `helpers.py` | subset (below) |
| `config.py` | `config.py` | subset; paths changed (below) |
| `signing.py` | `signing.py` | trimmed; pymobiledevice3 via subprocess (below) |
| `LICENSE-SideTap` | `LICENSE` | whole |

Tests came along too, adapted to the new import paths and the trimmed
surface: `tests/test_phone_driver_*.py`. They pin the measured constants
(tap hold 80 ms, idle wait 2 s, accessibility deadline 2 s, wait_for_text
0.25 s, wait_for_app 0.1 s, press_home 0.05 s poll under a 2.8 s ceiling,
wait_stable 0.15 s, the 30 s sleep-suspect window) and every unlock() incident.

## Upstream pin

- Repository: the local install at
  `%LOCALAPPDATA%\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\SideTap`
- Commit: **`0c75c53f672d07cfc8634969d246ece9194af37d`**
  ("site: the viewer card names Open apps")
- Copied on 2026-09-30 from the **working tree**, which carried ~155 lines of
  uncommitted local edits on top of that commit (see next section).

## Local (uncommitted upstream) patches that came along

From the working-tree diff of `src/phone_harness/device.py`:

1. **Stale userspace listener probe** in `tunnel_running()`: a `tunnel ls`
   entry with `userspaceTunPort` is only trusted once a TCP connect to
   `127.0.0.1:<port>` succeeds (go-ios keeps the record after the listener
   dies). Adds `import socket`.
2. **Dead phone route probe** in `tunnel_running()`: after the listener
   answers, `ios image list` must exit 0 within the remaining budget, so a
   listener whose RSD route to the phone is gone reads as *not running*.

Both are pinned by `tests/test_phone_driver_device.py`
(`test_tunnel_running_*`), copied from the working-tree `tests/test_device.py`.

Local patches that did **not** come along, and why:

- `device.start_tunnel()`'s new **12 s readiness cap** (`_TUNNEL_READY_TIMEOUT`
  3.0 → 12.0) and its "never established a reachable phone route" error:
  `start_tunnel` is not vendored at all (see *Trimmed* below); the supervisor's
  `Policy.entry_grace` (60 s) covers the same wait.
- `admin.py` (`_up` re-checks the tunnel before trusting `/status`, stops a
  dead managed tunnel before starting another): `admin` is not vendored. The
  link supervisor's `Decider` already encodes these rules.
- `viewer.py` (syslog capture opt-in): the viewer is not vendored.

## Trimmed or changed on purpose

**`config.py`**
- `STATE_DIR` is the app's own `.state/phone/` (under `VIDEO_DROP_STATE`), not
  SideTap's `.state`. One owner for pid files, logs, `wda_session`,
  `wda_bundle`, `apps_cache.json`, `agent_activity.log`, `STOP`.
- Reads this repo's `.env`. **Migration fallback:** if `PHONE_PASSCODE` /
  `WDA_BUNDLE_ID` are not set here, they are read from the installed SideTap's
  `.env` (found via `SIDETAP_ROOT` or the usual install paths). This is a
  config read only, never an import, and `config.legacy_keys()` names what is
  still borrowed so the setup check can say "copy these into .env".
  `VIDEO_DROP_NO_LEGACY_ENV=1` turns it off.
- New `GO_IOS_PATH` override for the go-ios binary. `PHONE_UDID` is the pin
  (`SIDETAP_UDID` still accepted).
- Dropped: send-approval settings, viewer port/poll, photos dir.

**`device.py`**
- Dropped every function that *starts* a go-ios process (`_spawn`,
  `start_tunnel`, `start_wda`, `start_forwards`). `video_drop/link_supervisor.py`
  is the only starter/stopper; it writes the pid/log files this module reads
  (`_pid_file`, `_log_file`, `proc_status`, `log_tail`, `stop_all`).
- Dropped the doctor's per-run memoization (`memoized_run`), the LAN/firewall
  checks (`port_exposed_to_lan`, `lan_block_rule_active`, `_netstat_ano_lines`),
  `running_apps` (`ios ps`), `lockdown_ready`, and `syslog` from `PROCS`.
- `ios_path()` honours `GO_IOS_PATH`, and a pinned path that does not exist
  raises instead of falling through. The sidetap.io installer location
  (`%LOCALAPPDATA%\SideTap\bin\ios.exe`) is no longer searched. Missing go-ios
  is one clear message: `device.GO_IOS_MISSING`.

**`helpers.py`** (subset the posting scripts call)
- Kept verbatim: `unlock()` and its helpers (`_passcode_pad_visible`,
  `_on_lock_screen`, `_pad_digit_probe`, `_scrub_secret`, `_pad_dismissed`,
  `_enter_passcode`), the window-size and tree memos, `press_home`,
  `open_app`/`close_app`/`_resolve_bundle`, `wait_for_app`, `wait_for_text`,
  `wait_stable`, `compact`, `type_text`/`set_clipboard` passcode refusals.
- Dropped: Messages (`send_message`, `send_image`, `read_messages`, thread
  walking), the send-approval gate and `trust` taint marks, `set_field_text`
  and the keyboard wait, Home Screen paging (`current_page`, `goto_home_page`,
  `find_on_home_screen`, `scroll_until_found`), `open_apps`,
  `save_clipboard_image`, `_cached_screen`.
- Added: `set_clipboard()` fills the `wda_bundle` cache via
  `device.detect_wda_bundle()` when it is empty, so the runner-foreground trick
  (`WDAClient._runner_foreground`) works on a fresh state folder.

**`wda_client.py`**
- Whole. The "run `phone-harness up`" hints now point at the link supervisor
  and `phone_link.recover()`; the STOP message names the file's real path.

**`signing.py`**
- **pymobiledevice3 is GPLv3 and is never imported into this process.** The
  misagent read (`_device_profiles`) runs a small script in a child
  interpreter (`sys.executable -c ...`) that writes the profiles to a temp
  folder; the parent reads the bytes. When the package is missing the error is
  `signing.PYMOBILEDEVICE3_MISSING` (a `SigningError`), not an `ImportError`.
  Rationale: a process boundary keeps this MIT app from linking GPL code,
  while the only misagent client on Windows stays usable for the human's
  re-sign step.
- The SideTap Pro identity hook (`SIDETAP_PRO_PATH`) is gone.
- `WDA_IPA` comes from `.env` (Phase 2 bundles the IPA and go-ios).
- After a successful re-sign the bring-up is handed to the link supervisor
  (`ensure_running` + `request_recovery` + `phone_link.wait_ready`) instead of
  SideTap's `admin.up()`.

`docs/ERRORS.md` mentioned in comments refers to SideTap's own repository.

## Still outside this package

- **go-ios binary**: resolved from PATH / npm global dir / `GO_IOS_PATH`.
  Bundling is Phase 2.
- **Signing WebDriverAgent**: a user step in Sideloadly (7-day free profile);
  `signing.fix_input` re-signs the nested `.xctest` with go-ios afterwards and
  needs `pymobiledevice3` installed (subprocess) plus `openssl`.
- **SideTap's viewer** (`127.0.0.1:8770`): optional. `server.py`'s
  `/api/sidetap` still summarises its doctor when it is running, and the link
  supervisor stands by while it runs (a second healer fights the first).
