# What's new

Changes and fixes per week, latest on top (last 10 weeks).

## Week of 26 October 2026

**New**
- Software updates: the box now updates itself from GitHub (optional weekly check), with an **Update now** button and a version display in Settings.
- Fresh-install script (`deploy/bootstrap.sh`) to set up a new Raspberry Pi with one command.
- **Play** tab: watch live TV, movies and series episodes from your phone or computer, with "Open in VLC" and download.
- Remote access: WireGuard VPN with DuckDNS, so you can use the web app away from home (Settings > Remote access, TV Menu > Remote access).
- Help and instructions page (`/help`), linked from each Settings section.
- Series: watched episodes (over 75%) and fully watched seasons are shown in green.
- Movies can be streamed to your phone/computer (Safari player, VLC, download).

**Changed**
- Remote access no longer uses a PIN; each device's VPN key is the access control.
- The web app now detects the box's own IP address instead of assuming a fixed one.
- README points to the QR code on the TV start page for opening the web app.

**Fixed**
- Desktop "Open in VLC" now downloads an `.m3u` file (the `vlc://` link only works on iOS).
- VLC/download links work over the VPN (signed token, since VLC has no browser cookie).
- Neater download-started message on the player page.

## Week of 19 October 2026

**New**
- First release of EasyIPTVBox: full-screen IPTV player for Raspberry Pi 5 with Argon IR remote and web remote.
- Boot and shutdown splash screen (Plymouth theme).
