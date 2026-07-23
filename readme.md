# Uma Launcher Global Helper

This public release is a Global-only helper launcher for the Steam version of Umamusume: Pretty Derby.
It listens for local [CarrotBlender](https://github.com/qwcan/CarrotBlender) packets and drives the
[GameTora](https://gametora.com/umamusume) training-event and skill pages.

## Retained features

- Steam launch, Global game-window tracking, single-instance locking, and shutdown after the game closes.
- Selectable Legacy and Modern training helpers with configurable table presets.
- A reusable current-run Events browser for browsing GameTora event chains.
- Skill planning and CM17 simulations.
- Local Trackblazer race scheduler, including its pinned GLPK solver runtime.
- Optional compressed training logs (`.gz`).
- Optional packet, race, veteran, friend-veteran, and race-schedule exports.
- Browser positioning, pairing, always-on-top behavior, and browser/driver overrides.
- User-triggered Discord error reporting.

Japanese/DMM support, translations, VPN automation, Discord rich presence, training CSV analysis, and
startup telemetry are intentionally not part of this release. Public Global releases continue to use the
built-in updater.

## Usage

1. Install the Global Steam game and CarrotBlender.
2. Download `UmaLauncher-Global.exe` from the latest public GitHub release.
3. Start the launcher. It will start the Global game through Steam if the game is not already running.
4. Start a career. The helper page opens when CarrotBlender sends the start packet.

Settings and logs are stored in `%AppData%\Uma-Launcher-Global`.

## Building

Use Python 3.12, install the exact versions in `requirements.txt`, then run `build_global.bat`. The build
script owns the single mandatory Peter Lowe ad-domain download. It makes one initial attempt followed by
up to three short retries, validates the result, and removes any stale staging snapshot before it starts.
If all four attempts fail, the build fails. The finished executable does not fetch the list at runtime.

The scheduler's GLPK 5.0.0 browser bundle is tracked under
`umalauncher/_assets/trackblazer_scheduler/vendor`; it does not load a solver or fonts from a CDN.

Tests do not launch the game, Steam, a real browser, or the Discord reporter:

```powershell
python -m unittest discover -s tests -v
```

## Disclaimer

This project is not associated with Cygames. Use it at your own risk.
