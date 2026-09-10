# Uma Launcher Global Helper

This public release is a Global-only helper launcher for the Steam version of Umamusume: Pretty Derby.
It listens for local [CarrotBlender](https://github.com/qwcan/CarrotBlender) packets and drives the
[GameTora](https://gametora.com/umamusume) training-event pages and the
[Bashin](https://bashin.app/visualizer/) skill visualizer.

## Retained features

- Steam launch, Global game-window tracking, single-instance locking, and shutdown after the game closes.
- Selectable Legacy and Modern training helpers with configurable table presets.
- A reusable current-run Events browser for browsing GameTora event chains.
- Ace simulations and an SS Rating purchase planner using published CM evaluations in Bashin.
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

The skill window defaults to Ace. Checking **Parent** opens the rating planner; unchecking it returns
to Ace. The checkbox and CM selection are remembered. Running-style overrides and planner choices
are retained for the current career.

- **Ace** uses actual trainee stats, aptitudes, acquired skills, and unique level with Great mood and
  2,000 iterations. Opening or selecting Ace, new hints, and newly acquired skills request a full
  evaluation. CM/style changes wait for **Run**. Hint-level, unique-level, stat, SP, and aptitude
  changes update local costs/ratings without triggering a simulation.
- **Parent** uses published CM values and actual trainee rating to recommend purchases toward
  **SS at 17,500**. The planner prefers useful CM skills and adds rating-efficient choices when needed.
  If SS is unreachable, it says so and recommends CM purchases without rating-only filler. **Select**
  marks hypothetical purchases and **Exclude** removes choices from the plan; excluded prerequisites
  block their upgrades. Unaffordable purchases cannot be selected; upgrades replace the selected lower
  rank when checking the budget. Saved choices are rechecked against current SP and costs.
  **Select Recommended** selects the recommended purchases and **Clear Choices** removes the constraints.
  CM/style changes update immediately; **Run** refreshes the inputs. This mode never invokes the
  executable. Recommendations are advisory and never buy skills in the game.

Parent shows remaining cost as **SP**, incremental rating gain as **Rating**, and **Pt/SP**. **Select** and **Exclude**
checkbox columns follow Skill. Checking one clears the other; leaving both unchecked lets the planner
choose automatically. Selecting a purchase reserves its cost and rating gain, then recalculates whether
the remaining budget can still reach SS. Recommendations appear in a compact skill list. Costs include
hint discounts and unpaid prerequisites; upgrade rating gains subtract the learned lower rank.
Ace shows **s/100 SP** immediately before Eff, using mean seconds saved divided by remaining cost,
multiplied by 100. Parent performance remains the published individual-skill baseline; planning subtracts acquired
ranks' CM values. Combined purchase values are estimates, not a simulation of the whole bundle.

The green **Run** button becomes a red **Stop** during an Ace evaluation, including after switching modes.
Existing results stay visible. New skill updates in Ace queue one latest follow-up without interrupting
the active run. Only explicit Stop or launcher shutdown terminates an evaluation; there is no time limit.
Rating/planning work runs separately from both packet handling and the simulator.

Skill definitions, names, ratings, costs, and prerequisite relationships come from the installed Global
database and trainee packets. Bashin supplies CM conditions, course geometry, visuals, and published
performance for Parent. Required sources must be available; missing performance is displayed as
unavailable. There is no bundled or downloaded skill-data fallback.

Bashin CM settings, course geometry, and published evaluations are cached under
`appdata/skill-simulator/bashin` and survive restarts. Opening/reloading the skill window and first
use of a CM's published evaluations check for updates; unchanged files reuse the cached copy.
Run, style changes, and switching back to a loaded CM use the data already loaded. Other CM evaluations
load only when selected. New or changed files use gzip
compression when available. If Bashin cannot be reached for a check, the error is reported.

## Building

For local visualizer development, serve Bashin's `static` directory and set
`UMALAUNCHER_SKILL_VISUALIZER_URL` to its full launcher URL (for example,
`http://127.0.0.1:8797/visualizer/?launcher=1`) before starting UmaLauncher.

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
