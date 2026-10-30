# Norrath Field Map

A companion map and quest tracker for **EverQuest Online Adventures** on the **Sandstorm** server. It runs in its own window beside the game (a second monitor works well) and never changes the game client.

## ⬇️ [Download the latest version](../../releases/latest)

On the release page, under **Assets**, download **`NorrathFieldMap.zip`**. You only need that one file, and it includes the maps. Everything else on this page is the source code.

### Getting started
1. Unzip it anywhere. Keep the `.json` files in the same folder as `NorrathFieldMap.exe`.
2. Run **NorrathFieldMap.exe**. You don't need Python or anything else.
   - The first time, Windows may say *"Windows protected your PC"*. Click **More info ▸ Run anyway**. Windows shows this for any app that isn't from a paid, signed publisher.
3. The maps of Tunaria, Odus and Oggok are included. There's nothing to download.
4. **Settings**: point *Game client* at your `eqoa.exe`, then use **▶ Launch game**.
5. When the map asks, click **Restart as administrator**. It needs administrator to follow your character (see below).

## What it does
- **Full-detail world map** of Tunaria, Odus and Oggok, with your pins.
- **Follows your character(s) live.** It supports several game windows at once (two-boxing) and lets you pick who to follow.
- **Quest tracker.** It has every class quest for levels 1–20, which tick off by themselves as you play. It also has the other quest guides: 17, 23–47, 30/40/49 path quests, 53–59, the level 60 epics, exemplars, weres, portals and more.
- **Click a quest step** to see where it is. The map zooms there and rings it.
- **Find box (Ctrl+F).** Type any NPC, creature, town, zone, camp, coachman or rare and the map rings it.
- **Places tab.** Coachmen with their routes, towns, XP camps by level and rare spawns, each with an on/off switch.

## How tracking works (and why it asks for administrator)
The map reads your position and quest journal from the game's own network traffic on your PC, the same way Wireshark does. It's read-only: it never touches the game, its memory or its files, and never sends anything.

Windows only lets a program read network traffic like this while it runs as administrator. So the first time, the map offers to restart that way, and after that Windows asks *"allow this app to make changes?"* each time it starts. You can turn this off under **Feed ▸ Start the map as administrator**, but then the map can't follow you.

If Windows Firewall asks about the map, click **Allow**. Otherwise no traffic reaches it.

> **Optional alternative:** if you'd rather not run the map as administrator, install [Npcap](https://npcap.com) (the capture driver Wireshark uses, default options). With Npcap installed, the map uses it automatically and doesn't need administrator.

## Credits
- Map art: [eqoa.live/map](https://eqoa.live/map) by Xylof / Sandstorm. The release zip includes their map tiles, so players don't each download them from eqoa.live. The tiles aren't in this source repository.
- Quest guides, map points, coachmen and camps: [EQOA-Data](https://github.com/Jadiction/EQOA-Data) by Jadiction and contributors (MIT).
- Some NPC locations come from another EQOA server project and can be slightly off on Sandstorm.

## For developers
Run from source with Python 3.12+: `run.bat` installs the requirements and starts the map. `build_exe.bat` builds `dist\NorrathFieldMap.exe`. `make_release.bat` builds it and makes `dist\NorrathFieldMap.zip` for a GitHub release.
