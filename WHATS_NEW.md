# What's new

Changes and fixes per week, latest on top (last 10 weeks).

## Week of 26 October 2026

**New**
- **Light theme** for the web app: Settings > Appearance lets you choose Automatic (follows your phone/computer), Dark or Light. The choice is remembered per device.
- The web app's **Play** tab now shows pictures too, in the same order as the TV (Movies, Live, Series): live categories and channels are tiles with your uploaded picture or a colored square.
- New TV start page: a thin menu on the left (Movies, Live, Series, with the QR code at the bottom) is always shown. The box remembers the last section and starts there (Live the first time).
- **Live** now shows picture tiles for categories and channels. Upload your own pictures in Settings > Live thumbnails; without one a colored square is used.
- Movies and Series now show **posters** instead of text lists, on the TV (poster grid) and in the web app's Play tab (with a filter box). Posters come from your provider and are cached on the box; items without a picture show their title.
- Software updates: the box now updates itself from GitHub (optional weekly check), with an **Update now** button and a version display in Settings.
- Fresh-install script (`deploy/bootstrap.sh`) to set up a new Raspberry Pi with one command.
- **Play** tab: watch live TV, movies and series episodes from your phone or computer, with "Open in VLC" and download.
- Remote access: WireGuard VPN with DuckDNS, so you can use the web app away from home (Settings > Remote access, TV Menu > Remote access).
- Help and instructions page (`/help`), linked from each Settings section.
- Series: watched episodes (over 75%) and fully watched seasons are shown in green.
- Movies can be streamed to your phone/computer (Safari player, VLC, download).

**Changed**
- Opening a live category now automatically refreshes only old or missing channel pictures.
- Cleaner TV pages: no selected-title line, no caption under the QR code and no hint text at the bottom; only a small "1/3" page number with an arrow in the bottom right corner when there is more than one page.
- The TV pages no longer show a "Live/Movies/Series" heading (the left menu already shows it).
- *Live thumbnails* upload card moved up in Settings (right below Live categories).
- Remote access no longer uses a PIN; each device's VPN key is the access control.
- The web app now detects the box's own IP address instead of assuming a fixed one.
- README points to the QR code on the TV start page for opening the web app.

**Fixed**
- After watching a live channel, going back to the picture grid continues the picture scan where it stopped.
- Starting a channel now stops the picture scan immediately, including a grab already in progress, so it no longer takes the provider's only connection.
- Desktop "Open in VLC" now downloads an `.m3u` file (the `vlc://` link only works on iOS).
- VLC/download links work over the VPN (signed token, since VLC has no browser cookie).
- Neater download-started message on the player page.

## Week of 19 October 2026

**New**
- First release of EasyIPTVBox: full-screen IPTV player for Raspberry Pi 5 with Argon IR remote and web remote.
- Boot and shutdown splash screen (Plymouth theme).
