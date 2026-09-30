# Screen-size independence audit

Reference phone: iPhone 16 Pro Max, iOS 26.7, 440 x 956 pt @3x. Question: do
the posting flows hold on other iPhones? Proven offline, without a second
device, by `tests/test_size_independence.py`.

## How it is proven

The harness moves each screen onto 375x667@2x (SE), 414x896@2x (11),
390x844@3x, 393x852@3x, 402x874@3x, 430x932@3x, 440x956@3x, and a
Display-Zoom-style 402x874 drawn into 1320x2868 px (a non-integer scale). It
uses two transforms:

- **Scaled.** Every element frame is scaled, and the screenshot is resized to
  the device's real pixel size. This is the worst case for hidden absolute
  geometry.
- **Pinned.** Controls keep their point size and stay pinned to the safe-area
  top or bottom (inset 20/0 on SE, 34 at the bottom on Face ID phones). Each
  screen is drawn anti-aliased at the device's own pixel density, which is how
  UIKit lays out.

The synthetic screens copy the frames recorded on the reference phone, with
no private names. `RecordedFixtureSizeTests` also scales the local
recordings in `.state/fixtures` (13 of them match a map) and skips when
those recordings are absent.

**What now passes on every size:**
- All five YouTube maps (trim, editor, details, visibility and audience) are
  identified correctly. Every locator resolves to the same control under both
  transforms.
- `Runner.act` walks trim → editor → details → visibility → details →
  audience → details, and each tap lands inside the intended control.
- Icon locators hold at @2x, @3x, the non-integer scale and half-point
  offsets. The templates are cropped once from @3x, the way `record_screen.py`
  crops them.
- Icon ambiguity still fails on every size.
- The legacy radio, trim-readiness, thumbnail, text-editor exit and share-rail
  geometry holds on every size.

## Sites

| Site | Before | Status | Fix / remaining work |
|---|---|---|---|
| `screens/matcher.py` id/label/type/relative + `within` (120 pt default) | point-space, from live tree | **safe** | Proven by harness (both transforms) |
| `screens/icons.py` `to_points` + NCC at point scale | resize to live `width x height` | **safe** | Proven at @2x, @3x, non-integer scale, ±0.5 pt |
| `screens/matcher.py` icon ambiguity | `IconError` → plain `MatchError`, so a `fallback` could narrow an ambiguous icon into a guess | **fixed** (not size, found while testing) | `AmbiguousIcon` → `AmbiguousMatch`; test on every size |
| `phone_youtube.py:299` `choose_unlabeled_radio` x=35 / x=28 | scaled by width (35→29.8 on SE, 31.0 on 390) | **size-dependent: broke on every size except 440** (ring sample hit background → "ring is not distinct" error) | Radio centre is a fixed point inset (measured 35.5 / 27.5 pt in recordings); now unscaled |
| `phone_youtube.py:314` `leave_text_editor` tap (20, 84) | proportional; it happened to land inside the 48 pt Back button (7 pt margin on SE) | **fixed** | Taps the live `Back` button's frame (`live_element`) |
| `phone_youtube.py:386` thumbnail first frame (17, 885) | proportional; 2.5 pt inside the slider's left edge on SE | **fixed** | `first_frame_point` from the live `Thumbnail frame selector` slider frame (left + 5 pt) |
| `phone_youtube.py:332` `wait_for_trim_next` pixel sample | proportional; only 36/54 samples inside Next on SE (threshold 0.6) | **fixed** | `PhoneLayout.bottom_right_point`: kept at a fixed distance from the right edge and the bottom safe area; 54/54 on every size |
| `phone_youtube.py:267` share-rail swipe y (392) | proportional; the share sheet is pinned below the safe-area top | **fixed when the app is exposed** | `share_rail_y` swipes on the app's own (even clipped) cell row. The proportional fallback remains only while the app is off-screen |
| `phone_ui.py:85` share rail band 0.3–0.55 h, clip 0.12/0.88 w | proportional | **unknown on SE** | The modeled rail lands at 0.51–0.53 h on SE, close to the 0.55 edge. It fails closed ("not safely visible"). Record one SE share sheet to confirm |
| `phone_youtube.py:188, 442` details scroll swipes (220,780↔350/300) | proportional | **safe** | All points fall inside the pinned scroll view on every size (test) |
| `phone_youtube.py:228` keyboard Search key band top ≥ 0.75 h | proportional | **safe** | The pinned return key sits at ≥ 0.89 h on every size (test) |
| `phone_youtube.py:447` Schedule band 0.3–0.8 h | proportional | **safe** | Schedule sits at 0.45–0.59 h on every size (test) |
| `phone_ui.py` `filled_radio` ±10/20 pt samples | point offsets, pixel scale from the image | **safe** | Proven on both themes at @2x/@3x/non-integer scale |
| `phone_focus.py:48, 60` Control Center swipes | top-right edge swipe | **size-dependent on Home-button iPhones (SE)** | On an SE, Control Center opens with a swipe up from the bottom. The top swipe opens Notification Center, and `focus_state` then raises `FocusError` (fails closed, before any media). Not fixed: the gesture is unverified. Until it is, SE users should leave the Do Not Disturb check off |
| `phone_instagram_preflight.py:52` handle band 0.06–0.12 h | proportional, but the nav bar is pinned to the safe-area top | **unknown** | No recording of the Instagram profile. On SE the band is 40–80 pt and a pinned handle would sit near 42 pt, at the edge. Fails closed. Record the profile header to confirm |
| `instagram_schedule.py:100-101` cover crop 0.166 w / 0.027 w | proportional | **unknown** | No recording of Scheduled content. If Instagram uses fixed-point thumbnails, the crop is 62 pt instead of 73 pt on SE, and the cover check fails closed as a false "does not match". Needs one Scheduled-content recording on another width |
| `instagram_schedule.py:70-71` caption window 0.13 h, x > 0.15 w | proportional | **safe (likely)** | The window is ≥ 87 pt on every size, and the caption sits a few lines above the time |
| `core.py:615` screenshot/layout aspect check | derived | **safe** | Holds at @2x, @3x and zoomed |
| `PhoneLayout.from_info` aspect 0.38–0.62 | derived | **safe** | Admits every listed iPhone |
| `youtube_nav.py`, `phone_onboard.py`, `phone_threads.py`, `phone_focus.tap_row` | row centres from the live tree | **safe** | — |

## Found while testing, not size-related

The first maps did not match the recorded YouTube build at 440 x 956 either:
`visibility.json` wanted a `StaticText` labelled exactly "Public" where the app
shows one `Button` ("Public, Anyone can search for and view"), and
`details.json` wanted "Select audience" after an audience had been chosen.
The screen-map update that followed fixed both: the visibility choices match a
`Button` whose label contains the choice, and the audience row is found by the
picker id with "Audience" or "Select audience" as its label.

## Still unproven without another device

The pinned model assumes these three things:

- The target apps pin controls to the safe area at fixed point sizes. The
  recordings support this: Upload Short, Next and the thumbnail slider all end
  a fixed number of points above the home indicator.
- The SE's safe-area insets are 20/0.
- The Dynamic Type size matches the reference phone.

A real non-440 recording of these would close the model gap:

- YouTube trim, editor, details, visibility and thumbnail editor
- the OneDrive share sheet
- Instagram's profile header and Scheduled content
- Control Center on a Home-button iPhone

Record them with `scripts/record_screen.py` and drop them into `.state/fixtures`. `RecordedFixtureSizeTests` then checks them automatically.
