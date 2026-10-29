"""The EasyIPTVBox instructions & documentation page, served at /help.

Each section has an id that the Settings page headers link to
(e.g. /help#movies)."""

HELP_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EasyIPTVBox - Instructions</title>
<style>
 :root{--bg:#12141c;--panel:#1e2130;--accent:#007acc;--accent2:#5ac878;--text:#e1e4eb;--dim:#8a90a0}
 body{margin:0;background:var(--bg);color:var(--text);
   font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.5}
 header{position:sticky;top:0;background:var(--panel);padding:.8rem 1rem;
   border-bottom:1px solid #2a2e40;z-index:2}
 header h1{margin:0;font-size:1.15rem}
 main{max-width:760px;margin:0 auto;padding:1rem}
 nav{background:var(--panel);border-radius:12px;padding:.8rem 1rem;margin-bottom:1rem}
 nav a{display:inline-block;margin:.15rem .7rem .15rem 0}
 section{background:var(--panel);border-radius:12px;padding:1rem 1.1rem;margin:0 0 1rem;
   scroll-margin-top:4rem}
 section:target{outline:2px solid var(--accent2)}
 h2{margin:0 0 .6rem;font-size:1.1rem}
 h3{margin:1rem 0 .3rem;font-size:.98rem}
 a{color:#5fb3ff}
 code,kbd{background:#2a2e40;border-radius:5px;padding:.05rem .35rem;font-size:.9em}
 table{border-collapse:collapse;width:100%;font-size:.92rem}
 td,th{border-bottom:1px solid #2a2e40;padding:.35rem .4rem;text-align:left;vertical-align:top}
 .tip{color:var(--dim);font-size:.9rem}
 .warn{border-left:3px solid #e8a33a;padding-left:.7rem;color:#e7d3b0}
</style></head><body>
<header><h1>EasyIPTVBox &ndash; Instructions</h1></header>
<main>
<nav>
 <a href="#remote">The remote</a>
 <a href="#tv">Using the TV</a>
 <a href="#play">Play tab</a>
 <a href="#movies">Movies</a>
 <a href="#series">TV series</a>
 <a href="#live">Live categories</a>
 <a href="#epg">TV guide</a>
 <a href="#appearance">Appearance</a>
 <a href="#playback">Playback</a>
 <a href="#remotes">Remotes</a>
 <a href="#wifi">Wi-Fi</a>
 <a href="#vpn">Remote access</a>
 <a href="#provider">IPTV provider</a>
 <a href="#trouble">Troubleshooting</a>
</nav>

<section id="remote"><h2>The remote</h2>
<table>
<tr><th>Button</th><th>What it does</th></tr>
<tr><td>Arrows</td><td>Move around. Hold an arrow to scroll quickly (about 3 steps a second).</td></tr>
<tr><td>OK</td><td>Open / play. While watching live TV: pause and resume. While watching a movie or episode: open the movie menu.</td></tr>
<tr><td>BACK</td><td>Go back one step.</td></tr>
<tr><td>HOME</td><td>Back to the start page.</td></tr>
<tr><td>MENU</td><td>On the start page: open the menu. In a live channel list: refresh the channel pictures.</td></tr>
<tr><td>VOL + / &minus;</td><td>Volume.</td></tr>
<tr><td>POWER</td><td>Switches the box off and on (handled by the Argon case).</td></tr>
</table>
<p class="tip">On the start page, MENU, BACK or HOME open the menu: Add a remote, Wi-Fi setup,
Remote access, Instructions, Standby, Power off.</p>
</section>

<section id="tv"><h2>Using the TV</h2>
<h3>Start page</h3>
<p>Movies and Series are at the top; your live categories are below in two columns.
The QR code in the corner opens this web app on a phone.</p>
<h3>Live TV</h3>
<ul>
 <li><b>UP / DOWN</b>: previous / next channel. If you started from the picture grid,
  only channels with a picture are used.</li>
 <li><b>LEFT / RIGHT</b>: jump 30 seconds back / forward (within what the box has buffered).</li>
 <li><b>OK</b>: pause / resume.</li>
 <li><b>MENU</b> in a channel list shows channel pictures; choose to refresh all or only old/missing ones.</li>
</ul>
<h3>Movies and series</h3>
<ul>
 <li>The box remembers where you stopped and asks to continue.</li>
 <li><b>OK</b> while watching opens the menu: Resume, Audio track, Subtitles, Jump to time, Stop.</li>
 <li>The audio and subtitle languages you pick are remembered; the most recent available one is chosen automatically next time.</li>
 <li>Episodes you have watched more than 75% of are shown in green; a season turns green when all its episodes are.</li>
</ul>
<h3>Busy</h3>
<p>The provider allows only one stream at a time. While a phone or computer plays
something, the TV shows <i>&ldquo;Busy &ndash; playing on another device&rdquo;</i>.</p>
</section>

<section id="play"><h2>Play tab (phone / computer)</h2>
<p>Plays your live channels, movies and series episodes on the device you are using.
Pick something from a drop-down; a player appears at the top.</p>
<ul>
 <li><b>In the browser</b>: MP4 movies and episodes play directly. Live TV plays in Safari
  (iPhone, iPad, Mac), about 10&ndash;20 seconds behind the broadcast.</li>
 <li><b>Open in VLC</b>: plays almost every format. On iPhone it opens the VLC app; on a
  computer it downloads a small <code>.m3u</code> file &ndash; open it with VLC.</li>
 <li><b>Download to this device</b> (movies and episodes): saves the file with its name. On
  iPhone it ends up in Files &rsaquo; Downloads.</li>
</ul>
<p class="warn">Playing or downloading here stops the TV. Only one thing can play at a time.</p>
<h3>VLC on iPhone</h3>
<p>Settings &rsaquo; Privacy &amp; Security &rsaquo; <b>Local Network</b> &rsaquo; switch <b>VLC</b> on.
Without it VLC opens but shows an error. If VLC isn't listed, open the VLC app once and allow its question.</p>
</section>

<section id="movies"><h2>Movies</h2>
<ul>
 <li>Type part of a title and press Search. All words must match, in any order
  (&ldquo;night summer&rdquo; finds &ldquo;Hot Summer Nights&rdquo;).</li>
 <li><b>Add</b> puts a movie in the Movies category on the TV. Remember <b>Save &amp; Apply</b>.</li>
 <li><b>Add future results automatically</b> (top of the results) saves the search. When the
  provider lists are renewed, new matches are added to Movies and the saved search is removed.</li>
 <li>Searches use the box's copy of the provider list, renewed every
  &ldquo;Provider lists refresh every (hours)&rdquo; (see <a href="#epg">TV guide</a>).</li>
</ul>
</section>

<section id="series"><h2>TV series</h2>
<p>Works like Movies: search, <b>Add</b>, then <b>Save &amp; Apply</b>. On the TV, open Series,
pick a series, a season and an episode. &ldquo;Add future results automatically&rdquo; works here too.</p>
</section>

<section id="live"><h2>Live categories</h2>
<ul>
 <li>Search the provider's categories and add the ones you want on the start page.</li>
 <li>You can give a category a short name (alias) for the TV.</li>
 <li>Categories with the same name are merged into one, with duplicate channels removed.</li>
</ul>
</section>

<section id="epg"><h2>TV guide (EPG)</h2>
<p>The box shows what's on now next to live channels. Guide sources are matched to your
channels automatically and refreshed every 24 hours.</p>
<p><b>Provider lists refresh every (hours)</b> controls how often the channel, movie and
series lists are downloaded again (only while nothing is playing). Saved searches are checked then.</p>
</section>

<section id="appearance"><h2>Appearance</h2>
<p><b>Text size on the TV</b>: Small, Normal, Large or Extra large. Text that is still too
long is shrunk to fit, and shortened with &ldquo;&hellip;&rdquo; as a last resort.</p>
</section>

<section id="playback"><h2>Playback</h2>
<p><b>Audio sync for live TV</b> shifts the sound relative to the picture, in milliseconds:</p>
<ul>
 <li><b>0</b>: no change.</li>
 <li><b>Negative</b> (e.g. &minus;100): sound earlier &ndash; use when the sound lags behind the picture.</li>
 <li><b>Positive</b> (e.g. 100): sound later &ndash; use when the sound comes before the picture.</li>
</ul>
<p class="tip">Takes effect the next time a channel is opened.</p>
</section>

<section id="remotes"><h2>Remotes</h2>
<ul>
 <li>Any ordinary <b>infrared</b> remote can be taught to the box. Test: point it at a phone
  camera and press a button &ndash; a visible flicker means infrared.</li>
 <li>Remotes marked &ldquo;Smart&rdquo;, with a microphone, or rechargeable (Samsung Smart Remote,
  Apple Siri Remote) are usually <b>Bluetooth</b> and can't be learned.</li>
 <li><b>Add a remote</b> (here, or Menu &rsaquo; Add a remote on the TV) opens a wizard on the TV: press
  each button it asks for. If the remote lacks a button, press the already-learned OK to skip it.</li>
 <li>All listed remotes work at the same time. Rename or delete them here.</li>
 <li>POWER isn't learned: the Argon case handles it.</li>
</ul>
</section>

<section id="wifi"><h2>Wi-Fi</h2>
<ul>
 <li>Lists every saved Wi-Fi network, best first, with whether it's in range and its last speed test.
  <b style="color:var(--accent2)">Green</b> marks the connection used for playing.</li>
 <li><b>Re-test connections</b> measures each saved network in range (the Wi-Fi drops briefly while switching) and Ethernet.</li>
 <li>At start-up the box joins the best saved network in range.</li>
 <li><b>Add / change Wi-Fi (on TV)</b> opens the Wi-Fi screen on the TV: pick a network and type
  the password with the remote. Saved networks connect without retyping.</li>
 <li><b>New place without network?</b> The box opens remote setup and Wi-Fi setup automatically at start-up.</li>
</ul>
</section>

<section id="vpn"><h2>Remote access (VPN)</h2>
<p>Use the box from anywhere through an encrypted WireGuard VPN. Only the box is reachable over
it &ndash; not the rest of your home network &ndash; and only traffic to the box goes through the VPN.</p>
<h3>Set up (once, from home)</h3>
<ol>
 <li><b>Router</b>: forward <b>UDP 51821</b> to the box's address (shown in Settings), port 51821.
  On a Sagemcom HomeBox: Access Control &rsaquo; Port Forwarding &rsaquo; Add Rules Manually;
  Protocol UDP; leave External host empty.</li>
 <li><b>DuckDNS</b>: enter your domain (e.g. <code>name.duckdns.org</code>) and token, then Save.
  The box keeps it updated every 5 minutes. The token is stored only on the box.</li>
 <li><b>PIN</b>: set a 4&ndash;8 digit PIN. It's asked only when connecting over the VPN.</li>
 <li><b>iPhone</b>: install <b>WireGuard</b>, then <b>Add device</b> in Settings and scan the QR code
  (WireGuard &rsaquo; + &rsaquo; Create from QR code). The code is shown once, also on the TV.</li>
</ol>
<h3>Away from home</h3>
<p>Switch the VPN on in WireGuard and open <code>http://10.66.0.1:8080</code> (add it to the Home Screen).
WireGuard uses your DuckDNS name to find home; the browser always uses 10.66.0.1.</p>
<p class="tip">Remote-access settings can only be changed from home. Five wrong PINs lock that
device out for 5 minutes. Remove a lost phone under Devices.</p>
<p class="warn">Live TV over mobile data uses roughly 2&ndash;8 GB per hour.</p>
</section>

<section id="provider"><h2>IPTV provider</h2>
<p>Enter the provider address and login. You can paste the full <code>get.php?username=&hellip;&amp;password=&hellip;</code>
link instead; then the username and password fields can stay empty. Press <b>Save &amp; Apply</b>.</p>
<p class="tip">The provider allows one connection at a time: the TV, the Play tab and downloads share it.</p>
</section>

<section id="trouble"><h2>Troubleshooting</h2>
<table>
<tr><th>Problem</th><th>Try</th></tr>
<tr><td>The remote does nothing</td><td>Aim at the case's IR window from closer. Check the batteries. Learn it again (Remotes).</td></tr>
<tr><td>&ldquo;Busy &ndash; playing on another device&rdquo;</td><td>Stop playback on the phone/computer; the TV is free a few seconds later.</td></tr>
<tr><td>A channel won't start right after another device stopped</td><td>Wait up to a minute: the provider still counts the old connection.</td></tr>
<tr><td>VLC opens but plays nothing (iPhone)</td><td>Allow Local Network for VLC (see <a href="#play">Play tab</a>).</td></tr>
<tr><td>Can't connect over the VPN</td><td>Check the router port forward, DuckDNS (Settings &rsaquo; Check setup), and that the VPN is on in WireGuard.</td></tr>
<tr><td>Picture stutters or freezes</td><td>Make sure the box is cool and not covered. 4K channels need a good connection.</td></tr>
<tr><td>Can't find the web app</td><td>Scan the QR code on the TV's start page.</td></tr>
</table>
</section>
<p class="tip" style="text-align:center"><a href="/">&larr; Back to the web app</a></p>
</main></body></html>"""
