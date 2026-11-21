# Debug mode

Started with `launch_debug.bat` (it asks for the game) or with `--debug` on any launcher (`launch_dq6.bat --debug`, `python -m dq456_pc --game dq6 --debug`).
Without it, none of this runs.

Everything is applied on the window's thread, between two engine steps: the engine is never touched while it runs.

## Keys

| Key | Effect |
|---|---|
| F2 | overlay on / off (top left) |
| F3 | pause / resume: no engine step, sound paused, the picture stays |
| F4 | one engine step (pauses first); Shift + F4: 10 steps |
| F5 / F6 | game clock slower / faster: x0.1, x0.25, x0.5, x1, x2, x4, x8, x16 |
| F7 | back to x1 |
| F8 | bug report (below) |
| F9 | opens the web panel in the browser |

The speed multiplies the game's own (accelerator, battle speed). Above what the computer can compute, the steps of a displayed frame are cut by the usual budget:
"faster" is then as fast as the machine allows (the engine is emulated: a few times real time, not hundreds).
The debug keys take precedence over the Controls in debug mode, except while the Esc menu is open.

## Overlay

Engine frame and loop frame, game part, map, the shared state word (`Global.Flag`) with its known bits named (fade, effect, lock, pad, map, ui lock, busy, bgm),
both fade planes (opacity, target, frames left), the menu that has the input (port, window type, cursor), whether a message is shown, the pace (engine steps per second,
duration of an engine step, of a drawing), and the last debug notes.

## Bug reports (F8) and their replay

While the debug mode runs, the port keeps in memory a savestate every 300 engine steps (10 s; the 4 last ones) and, for each engine step since the oldest one,
what the window gave the engine: the keys, the menu messages sent between two steps, and the interface -> engine messages of the step (camera, map objects,
ends of animations: they come from the window's clock and camera).

F8 writes `data\logs\bug_<game>_<date>.zip`: the oldest savestate kept (10 to 40 s before F8), the steps since, a savestate of the moment, the screenshot,
the settings, the end of the engine's log and of the window's error log, and `info.json` (game, port version, map, frames).

Replay: `launch_debug.bat 6 --debug-replay data\logs\bug_dq6_<date>.zip` (or `--debug-replay` on any launcher). The report's settings are used (a copy: your
own file is not touched), its first savestate is loaded, then each step gets the recorded keys and messages (the window's own menu messages are dropped
meanwhile); at the end the game is paused (F3 to go on, F4 step by step). The engine gets exactly the same inputs: same positions, messages, menus and camera
at each frame (checked on 525 frames of DQ IV: walking, camera, a church's menus). What the recording does not hold (the Esc menu, a savestate loaded
during the recording) is not replayed.

## Web panel (F9)

Served on this computer only: http://127.0.0.1:8765 (`--debug-port N` to change it, `--debug-port 0` for none; another free port is taken if 8765 is busy:
the overlay shows the address). Meant for a second screen beside the game; it asks the game twice a second.

| Tab | Contents |
|---|---|
| State | the overlay's values, the end of the engine's log; in the header: pause / resume, +1 / +10 / +60 steps, speed, bug report |
| Scenes | the scene catalog (below) |
| Packets | the engine -> interface packets of the last 240 steps (type, first 12 integers, first 8 floats), filter by type (`22 28`, `!22 !28 !29`) |
| Menus | every menu port: window type and name, shown or hidden, engine cursor / local cursor, buttons (number, gamepad bits, place), lines; the one with the input highlighted |
| Objects | the characters (slot, id, position, visibility, pose, direction, scale), the meshes, the camera |
| Memory | read by symbol (`_ZN16TownPlayerAction9walkSpeedE`), absolute address (`0x...`) or offset in the library (`+0x2714bc`); write bytes (asks first) |
| Savestates | slots 1 to 9 of the game: save, load |

The server never touches the engine itself: everything goes through the debug service's queue, applied between two engine steps.

## Scene catalog (panel, "Scenes" tab)

To reach what needs testing without playing up to it. What it changes is the engine's own data, through the game's own functions:

- **Map**: any town or dungeon of the game data (`assets/map/<name>.stg`, a drop-down list; each map named after the first story step that goes there), entered as the game sends the hero through a link: `setExtraExitTown`
  (DQ IV, DQ V) / `setDirectLinkTownSurface` (DQ VI), the functions of the floor exits and of Evac (`StageLink::setTownExitIndex`, then `Global::startTown`).
  The hero arrives at the map's most obvious entrance, read from the game's links (`assets/map/map_link.bin`): the one from the world map, or else from the
  nearest map. A map no link leads to (event maps): its origin.
  **World map**: any of the game's worlds (DQ IV 3, DQ V 4, DQ VI 4: the field type of each place's symbol, `getFieldTypeBySymbol`), in front of any place on it
  that a map leads out to (a drop-down list, by world), or in front of the place the hero is in (or the nearest place that leads out to it), as Evac does
  (`setExtraExitField` / `setDirectFieldSymbol`); `Global::startField()` from the world map or from a place no link joins to it.
  A jump stops the looping sound effects of the place left (a clock's ticking): the scripts that would have stopped them are skipped.
- **Story step**: the game's table of steps (DQ IV 347, DQ V 349, DQ VI 183). `status::Status::setEventFlag(step)` clears the global switches and sets those of
  that step; `setEventParty(step)` puts the party of that step. The games give each step its map and a name in Japanese (DQ IV: `param/param_player_event1-4.dat`,
  DQ V / VI: `<game>ds_event_list.dat`): the panel shows French names (`dq456_pc/debug/story_steps_fr.py`: places, characters and items as the games' French
  texts name them, a plain description where they name none); "go to its map" starts it. Items, levels and gold do not change: a real save is still the
  reference to check a whole playthrough.
- **Items and gold**: any item of the game (with a search field; the names are the engine's own, `TextAPI::extractText(0x4000000 | number)`, as its menus show
  them: the message file's entries are not numbered like the items), 1 to 99 at once, each given as the game
  gives an item it finds: `PartyStatus::giveItem` (DQ IV, on `g_Party`) / `PartyStatusUtility::giveItem` (DQ V, DQ VI): the first member with room, otherwise
  the bag. Gold added or taken (negative amount) through the scripts' own "gold" command (`cmd_set_gold` / `cmdSetGold`), so the game's limits apply.
- **Switches**: the three sets of 1,024 the scripts test (`status::g_GlobalFlag`, `g_AreaFlag`, `g_LocalFlag`): the ones on are listed (a click turns one off), any
  number can be turned on or off.
- **Fight**:
  - a boss (an encounter row a script starts; the list gives its monsters' names and its map) is fought at its own place, from anywhere: the map whose script
    starts it is loaded first (`assets/script/<map>.bin`), then, once it is shown and the player free, `Encount::forceEventBrew(row)` as the script does
    (DQ IV: `BattleScriptManager::setEncountMap(row)` first, as `cmd_encount`): the backdrop is the place's own;
  - any other fight only where the game itself could start a random encounter at that moment, the whistle's conditions: random encounters allowed on the map
    (`StageStatus` / `StageAttribute::isEncount`: never in a town), not switched off by the story (the `Encount` byte a script's "encount disable" clears),
    an encounter row where the hero stands (`Encount + 8`; DQ IV also `Encount::isEncountStage`). Then `Encount::forceEncount()`: the next update starts it as a
    random encounter, with its backdrop (the world map's ground included). Empty row: the group of the place; another row: for that fight only.
    The panel says why a fight is refused.
  - any random encounter group of the game (the list gives each row's monsters) fought **here**, from a town too: `Encount::forceBrew(row)`, the scripts' own
    ordinary fight (`cmd_encount`, mode 0), during the next step;
  - the **backdrop** drawn behind the next fights: any battle scenery of the game data (the names that start with "btl"), or each fight's own. Drawing only: the
    engine's fight is the same.
- **Mini-games and book**, started from where the hero is, as the game starts them: DQ IV / V the scripts' `cmd_mini_game` / `cmdMiniGame` (0 poker, 1 slot
  machines), DQ VI `Global::setMinigame` then `startCasino`; DQ V's slime race and slime touch, DQ VI's slime curling by their `Global::start...`; the monster
  book by `Global::startBook`.

The calls that start a scene are made during the next engine step, just before GameUpdate (`Engine.step_calls`), so that their packets (the fade, a fight's
music) are read with the step's: the engine's output cursors are rewound first, as GameUpdate does (it would write over them).

## Party (panel, "Party" tab)

`dq456_pc/debug/party.py`, through the game's own functions:

- **Members**: each member's level and experience. The level is set by `HaveStatusInfo::levelup(level)`, the call the game makes for a character joining at a given
  level (the stats of that level, its experience threshold, the spells learnt up to it); the experience by `HaveStatusInfo::setExp`.
- **Joining and leaving**: any character the game has a name for (TextAPI kind 5: DQ IV's 20, DQ V's people and monsters, DQ VI's), through the scripts' own commands
  (DQ IV `cmd_set_party_join` / `cmd_set_party_quit`, DQ V `cmdCharacterSetPartyJoin` / `...Quit`, DQ VI `cmdSetPartyJoin` / `...Quit`), during the next engine step.
- **Money and play time**: gold, bank, casino tokens, mini medals through `BasePartyStatus`'s setters (their limits apply), the play time (1/60 s) through
  `GameStatus::setPlayTime`; DQ IV's adventure record too (battles, victories, wipeouts, escapes, monsters defeated, gold earned, highest damage, title...,
  `status::BattleHistory`'s getters and setters; the chapter's counters are the current chapter's).
- **Every item × 99**: `HaveItemSack::add(item, 99)` on the bag for every item the game names, the ones the game's own `HaveItemSack::fill` leaves out included.

## Monster book (panel, "Book" tab)

`dq456_pc/debug/book.py`: the book is the game's `status::g_BattleResult`, one entry of 6 bytes per page (times defeated, times its item was obtained, a level byte)
and a "met" bit per page, the bits the book's completion counts (DQ IV 210 pages, DQ V 202, DQ VI 197). Each page's monster: DQ IV's own table, DQ V / VI
`BattleResult::getMonsterIndex`; its name is the engine's.

- **One monster**: ticked, it is put in the book through the game's setter (`setEncount` / `setMonsterEncount`), counted defeated once and its item obtained once
  when it had neither, so its page shows everything; unticked, it is taken out and both counts go back to 0. A search field filters the list.
- **The whole book**: every page at once (the book at 100 %), or the book emptied.
