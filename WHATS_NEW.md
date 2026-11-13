# What's new

Changes and fixes per week, latest on top (last 10 weeks).

## Week of 9 November 2026

**New**
- **Movie fact sheet** (issue #14): OK on a movie (TV) or a tap on it (Play tab in the web app) first opens a page with the poster, year, length, genre, rating, description, director and cast, with a Play button. The details come from the provider (TMDB data); when the provider has no description, a short summary from Wikipedia is used. Details are remembered, so each movie is looked up only once.
- **Update check** (issue #15): Settings > Software updates now has "Check for updates". It stays on the same place and says "No updates available. You are on the latest version." or lists what is new with an "Install update" button; after installing it shows the result.
- **Provider list refresh** (issue #16): the setting moved from the TV guide card to the IPTV provider card, can not be lower than 8 hours any more, and the refresh now waits until nothing is playing on the TV or streamed to a phone. The TV guide itself (and extra guide addresses) always refresh every 24 hours.
- **Simple mode with movies and series** (issue #9): the simple-mode list can now also hold saved movies and whole seasons of series. They continue where you stopped, with no question and no pause, and only the title is shown for a moment. When a movie or episode ends, the next item in the list starts by itself (after the last one the list starts over). The buttons work as for live channels, and all seasons of a series count as one item: the buttons jump to the next channel / movie / series, and the episodes (and the next season, after the last episode) play one after another by themselves.
- **Movie folders**: Settings > Movie folders > Browse all movie categories adds a whole provider movie category to Movies as a folder (issue #13). On the TV the folders come first, shown as a collage of their first posters with the number of movies; OK opens the folder and shows all the movies in that category. The first 150 posters of every folder are downloaded in the background (low priority, and from a smaller picture size), the pictures you scroll to are fetched first.
- **TV guide while paused** (issue #11): on a live channel, OK now pauses and shows the guide for the next hours (the programme on now in green); OK again resumes.

**Fixed**
- Volume and mute are now remembered when you switch channel or start a movie, until the box restarts. Before, every new stream started at full volume (issue #12).
- The movie / episode menu (Resume, Audio track, Subtitles, Jump to time, Stop) is now kept on screen reliably: it is re-sent a few times right after OK and refreshed while it is open, and a failed command to the player is retried instead of lost (issue #10).

## Week of 26 October 2026

**New**
- Newly added movies and series (in the web app, or found by a saved search) now go to the top of their list, and a movie or an episode you start watching moves to the top too (issue #8).
- **Simple mode** (Settings > Simple mode): for someone who just wants to watch TV. The box boots straight onto a channel you picked (from several categories, in your order) and every remote button except volume and mute switches channel: Left goes to the previous one, all others to the next. Your changes only take effect after you press **Apply**, and turning it on shows a warning with how to switch it off again. It remembers the last channel, skips channels that don't work, and the power button still works.
- **TCP mode** for remote access (Settings > Remote access): a second way to reach the box from networks that block WireGuard. It uses VLESS + Reality (sing-box) on a TCP port you choose (default 8443), works with the sing-box, Hiddify and v2rayNG apps, only reaches the box, and is off until you turn it on.
- **Profiles**: several people can have their own Movies, Live and Series categories, theme, text size, watch history and live pictures. Settings > Profile switches the profile in the web app (the Default profile is always shown and the TV starts on it), and Settings > Manage profiles adds, renames, protects with an optional 4-6 digit PIN, gives a picture to, or deletes profiles; a new profile can start empty or as a copy. On the TV the profiles are listed above the QR code. Provider login, TV guide, audio sync, remotes and Wi-Fi stay shared.
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
- The TV power menu now starts with **Power off** (then Standby), so pressing Power twice (Power, then OK) turns the box off without looking at the screen (issue #7).
- The cards in the web app's Play tab (Movies, Live TV, Series) now start collapsed: tap a title to open or close it, or use Open all / Close all. Your choices are remembered in the browser, and starting something from a card opens that card (issue #6).
- Settings: the note "Provider, TV guide and audio sync are shared by all profiles." now sits in the Manage profiles card instead of under the profile selector (issue #5).
- TV menu: the profiles are no longer listed above the QR code. Only the current profile's picture and name is shown there; select it and press OK to open a list of profiles to switch between.
- The cards on the web app's Settings page are now **collapsed by default**: tap a title to open or close it, or use Open all / Close all. Your choices are remembered in the browser. The Profile selector always stays open. (Thanks for the suggestion, issue #2.)
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
- Remote setup: a USB remote or air mouse (2.4 GHz dongle) works as a keyboard and needs no learning. The setup wizard only listened for infrared, so it stalled on the second button; it now notices a USB remote, stops, and says it needs no setup. The menu key of such remotes now opens the menu too (issue #1).
- The stretched-picture fix sometimes did not apply right after a movie started (the player read the video's shape too early and skipped it). It now waits for the shape, checks that the player accepted the settings, and retries.
- Simple mode (and any settings change): the TV became very slow and the screen seemed to restart when pressing OK. Every save in the web app started another heavy TV-guide matching job, and a dozen of them ran at once. The guide is now rebuilt by one job at a time, and only when the list of live channels actually changed. In simple mode the boot splash is no longer shown again when the list opens.
- Quick, overlapping saves in Settings could garble `config.yaml` (two saves wrote to the same temporary file at once). Saves are now serialised and use their own temporary files.
- Movies and episodes whose file is tagged 16:9 but whose picture is wider (for example 3840x1754) no longer look stretched vertically: the player shows them at their real shape, with black bars above and below.
- Movies and episodes with a padded bottom edge no longer show a thin green line: the player trims up to 15 extra rows (to a multiple of 16) automatically, and keeps hardware decoding.
- The "Download the config / sing-box profile" buttons did nothing on many phones. They now make a real file, and there are new **Share / open in the app** and **Copy the text** buttons as alternatives (the profile or config can be pasted into the app).
- TCP mode: the box is now reached at **http://192.0.2.1:8080** instead of 10.66.0.1. Apps like Hiddify keep private addresses (10.x) outside the tunnel ("bypass LAN"), so the page never opened. The tunnel status in Settings now also shows when the last page request came through. Updating restarts the service.
- TCP mode: apps that multiplex their connections (Hiddify) connected but got no data, because the box did not accept multiplexing. It does now; updating restarts the service with the new setting.
- TCP mode: the service crashed at start on the Pi (its security settings blocked a socket type sing-box needs), so connections timed out. Fixed; updating also repairs an already-enabled TCP mode automatically.
- The TV guide is shared by all profiles: the automatic guide is now built from the channels of every profile, so switching to an empty profile no longer leaves it without guide data.
- Saved searches ("add future results automatically") now show the selected profile's own list; after switching profiles the web app kept showing the previous profile's searches.
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
