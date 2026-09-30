"""The app's own iPhone driver: go-ios + WebDriverAgent over USB.

Vendored from SideTap (MIT, (c) 2026 Wes Sander); provenance, the pinned
upstream commit and every local change are in VENDORED.md beside this file,
and the license text is in LICENSE-SideTap.

Modules (import the one you need; nothing here spawns a process on import):

- ``config``      ports, the app-owned state folder (``.state/phone/``), .env
- ``device``      go-ios: device/app lists, tunnel + DDI probes, pid bookkeeping
- ``wda_client``  the WebDriverAgent HTTP client and its shared-session model
- ``capture``     screenshots (WDA when up, go-ios otherwise)
- ``helpers``     what the posting scripts call: tap/swipe/type, unlock, open_app
- ``signing``     re-sign WebDriverAgent after Sideloadly (a user step)

Only ``video_drop.link_supervisor`` starts or stops the go-ios processes.
"""
