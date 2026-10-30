NORRATH FIELD MAP  (desktop version)
====================================

First run
  1. Install Python from python.org (tick "Add python.exe to PATH").
  2. Double-click run.bat. The first time it installs PySide6 (a minute or two).
  3. In the app: Map > Download Tunaria map. It resumes if you stop it.

Make an .exe
  Double-click build_exe.bat. The app appears in dist\NorrathFieldMap.exe.
  Keep "maps", pins.json and settings.json in the same folder as the exe.

Using it
  Mouse wheel zooms, drag pans, Home fits the whole map.
  Bottom-left shows the game X / Z under your cursor.
  "Add pin" then click the map. Drag pins to move them. Edit in the side panel.
  Type X and Z in the toolbar and press Go to mark where you are.
  "On top" keeps the window above the game.

Tracking your character (no setup in the game)
  The map reads your position from the game's own network traffic on this PC,
  read-only, like Wireshark. It never touches the game, its memory or files,
  and never sends anything. Use the normal, unmodified client.

  Setup, either way works:
    A. Nothing to install: when the map starts without Npcap it offers to restart
       as administrator and uses Windows' own packet capture. Windows asks "allow
       this app to make changes?" each time it starts. If Windows Firewall asks,
       allow it (otherwise no traffic reaches the map).
       Feed > "Start as administrator when Npcap isn't installed" turns this on/off.
    B. Or install Npcap from npcap.com (the capture driver Wireshark uses, default
       options). Then the map needs no administrator. If Npcap is installed, the
       map always uses it.
  (Npcap's license doesn't let other apps bundle its installer, so the map can't
  include it; option A is the built-in alternative.)
  Feed > Track the game automatically is on by default.

  Easiest: Settings (next to Map) > Game client: point it at your eqoa.exe, then use
  ▶ Launch game in the toolbar (click again for a second window). Or tick "Launch the
  game when the map opens". The map then knows which program is the game and listens
  to its traffic only. It also finds the game by itself if it's named eqoa... or PCSX2.

  Every time:
    OPEN THE MAP BEFORE YOU LOG IN to a character. The game sends your full
    position once as you enter the world, then only small changes. If the map
    starts after that, it says so: Quit to the server list and log back in.

  Several game windows: each is its own character, named from the window title
  ("EQOA 1 - Dimitri" shows as Dimitri). PCSX2 works too and shows as "PCSX2".
  The arrow learns which way you face from the direction you walk, so it can
  take a few steps of running before it points right.

  Not picking you up? Feed > Tracking details shows what the capture sees and what to do.
  Or outside the map:  python gamefeed.py   (prints positions and a report every 5 s)

  Playing beside the map: clicking, dragging and zooming the map leaves the game focused,
  so your keyboard and controller stay with the game (Map > Clicking the map leaves the
  game focused). Typing in a box borrows the keyboard and gives it back on Enter or Esc,
  or when you move the mouse off the map.

Several characters
  Each character gets its own coloured arrow, name and trail.
  Follow (toolbar): the map keeps that character in view and switches worlds with it.
    The first character to appear is followed until you pick one; your pick is remembered.
    Pick "Nobody" to look around freely. Changing the world by hand also stops following.
  A character that stops sending turns grey ("not sending") and drops off after 5 minutes,
    or right away with Feed > Forget characters that stopped sending.
  Worlds without a downloaded map (Lavastorm, Plane of Sky...) still show in the Follow list.

Test feed (no game needed)
  python feed_demo.py, then Feed > Connect feed. Two made-up characters walk in circles.
  More options: python feed_demo.py --help

Quest tracker (right-hand panel)
  Character: follows whoever the map is following. Set race, class and level once;
    the home city is picked for you (humans have two, pick yours).
  The list is your class's quest chain by level:
    ✓ done   ▶ in progress   ○ you can take it now   · not yet (level, or earlier quests first)
  Click a quest for its giver, what they say, the steps and the rewards.
    Click the giver or a step to see where it is (rings on the map; following pauses 20 s).
    Start a quest, tick steps as you do them; the last tick marks it done.
    Mark done also marks the earlier quests of the chain done.
  On map: gold ! where you can pick up a quest, teal ? where your next step is.
  Progress is saved per character in quest_progress.json.

  Automatic (with Track the game on): when you log in, the map reads your character's
  race, class, level and quest journal from the game's own traffic, then keeps up as
  you play: quests start and their steps tick as your journal changes, and a quest is
  marked done when the game says "You have finished a quest!". Levelling up updates
  the level. Log in with the map open (or use ▶ Launch game) so it sees the journal.
  Sandstorm words its journal its own way, so now and then a step may be guessed one
  off; your own ticks and buttons always work and the map won't undo a "Mark done".
  Quest data: EQOA-Data (github.com/Jadiction/EQOA-Data, MIT) via the EQOA Rust
  client's quest guide. NPC places come from another EQOA server project and can be
  a little off on Sandstorm.

Find (the box in the toolbar, or Ctrl+F)
  Type part of any name: an NPC, creature, town, zone, camp, coachman, rare or one of
  your pins. Pick it from the list (or press Enter for the top match) and the map
  switches world, zooms there and rings it. Several spots: the nearest ones to you.

Places (the tab beside Pins)
  Tick Coachmen, Coach routes, Towns, XP camps or Rare spawns to show them on the map.
  Click a coachman on the map to light up the towns it runs to (click again to clear).
  "Camps for level" shows only camps within 3 levels of the number you set.
  Click any entry in the list to find it on the map. Data: EQOA-Data (Jadiction, MIT).

Feed format (for other tools), one JSON message per update, via Feed > Feed addresses:
  {"char": "Dimitri", "world": "Tunaria", "x": 25123.4, "z": 14002.7, "heading": 1.57}

Files
  maps\      downloaded map tiles (Tunaria is a few hundred MB at full detail)
  pins.json  your pins. Copy this file to share pins, or use Export/Import.
  quests.json            the quest data (keep it next to the app or the exe)
  atlas.json             every named place for the Find box (zones, towns, NPCs, camps)
  guides.json            all the other quest guides (17, 23-47, path quests, epics, portals...)
  quest_progress.json    your quest progress per character
  places.json            coachmen, routes, towns, XP camps and rares (keep it next to the app)

Map art: eqoa.live/map by Xylof / Sandstorm. Please don't redistribute the tiles
without their OK. Share the app and pins.json instead; others can download the map themselves.
