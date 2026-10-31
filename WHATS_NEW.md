# What's new

Changes and fixes per week, latest on top (last 10 weeks).

## Week of 26 October 2026

**New**
- Settings in the web app now **save by themselves**: the Save & Apply and Cancel buttons are gone. A small "Saved" note appears when a change is stored. If you change a setting while watching TV, the picture is not interrupted; the TV applies the change when you leave the stream.
- **Play on TV** button in the web app's Play tab (above "Open in VLC"): starts the chosen channel, movie or episode on the TV. Movies and episodes continue from where you stopped.
- The Play tab now shows channel pictures for live categories. Opening a category there also starts the picture scan on the box, as long as nothing is playing on the TV; pictures appear as they are made.
- **Light theme** for the web app and the TV: Settings > Appearance > Theme (Dark or Light). One setting for both, saved with the other settings.
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
- The box now checks how many simultaneous connections your provider allows (at start and with every provider refresh) and uses it for the picture scan: with 1 connection it scans one channel at a time, with more it scans several in parallel (always leaving one connection free for watching).
- The picture scan starts each grab a little faster (smaller stream probe).
- In the dark theme the TV's QR code is now dark too (light squares on a dark background).
- The picture-scan text now shows which page is being scanned ("Scanning pictures on page 2/21") instead of a channel count.
- The picture-scan progress ("Scanning pictures 34/208") is now a small text at the bottom left instead of next to the category name.
- Opening a live category now automatically refreshes only old or missing channel pictures.
- Cleaner TV pages: no selected-title line, no caption under the QR code and no hint text at the bottom; only a small "1/3" page number with an arrow in the bottom right corner when there is more than one page.
- The TV pages no longer show a "Live/Movies/Series" heading (the left menu already shows it).
- *Live thumbnails* upload card moved up in Settings (right below Live categories).
- Remote access no longer uses a PIN; each device's VPN key is the access control.
- The web app now detects the box's own IP address instead of assuming a fixed one.
- README points to the QR code on the TV start page for opening the web app.

**Fixed**
- Settings autosave could overwrite your categories with an empty list if the page was hidden before it finished loading. It now waits until the settings are loaded, the box refuses a save that would remove all categories, and the last 10 versions of the config are kept as dated backups.
- "Play on TV" no longer says "Another device is streaming" because of the web page's own preview: it now stops the preview first and takes over.
- The picture scan no longer retries channels that gave no picture within the last hour, and an interrupted scan continues where it stopped.
- A live stream that fails to start or stops (black screen) is now retried automatically up to three times, then the box returns to the channel list with a message.
- After watching a live channel, going back to the picture grid continues the picture scan where it stopped.
- Starting a channel now stops the picture scan immediately, including a grab already in progress, so it no longer takes the provider's only connection.
- Desktop "Open in VLC" now downloads an `.m3u` file (the `vlc://` link only works on iOS).
- VLC/download links work over the VPN (signed token, since VLC has no browser cookie).
- Neater download-started message on the player page.

## Week of 19 October 2026

**New**
- First release of EasyIPTVBox: full-screen IPTV player for Raspberry Pi 5 with Argon IR remote and web remote.
- Boot and shutdown splash screen (Plymouth theme).
