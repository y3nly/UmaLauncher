# Uma Musume Trackblazer Scheduler

A browser-based race schedule optimizer for Uma Musume's Trackblazer mode. Plan your entire Junior through Senior year race schedule to maximize stats, skill points, and epithet completions.

Originally created by [SkyeNat21](https://github.com/SkyeNat21/umamusume_trackblazer_scheduler), forked by [daftuyda](https://github.com/daftuyda/umamusume_trackblazer_scheduler).

**Live site:** [race.daftuyda.moe](https://race.daftuyda.moe)

---

## Features

### Solver

The core of the app is a Mixed Integer Linear Programming (MILP) solver that runs entirely in-browser via [GLPK.js](https://github.com/jvail/glpk.js) (WebAssembly). It optimizes across all 75 race windows to maximize a weighted score of race stats, skill points, epithet rewards, and hint bonuses.

**Constraints the solver respects:**

- Distance and surface aptitude eligibility
- Maximum consecutive race limit
- Forced epithet requirements (hard constraints; returns infeasible if impossible)
- Manual race locks and training blocks
- Summer training defaults
- Conditioning penalty for 3+ races in a row

### Schedule Interaction

- **Confirm Race** — Lock in a race you've won; freezes everything up to that turn
- **Lost — Retry** — Didn't get 1st? Marks the turn as training so the solver replans the race at a later turn
- **Skip — Train** — Lock a turn as a training turn (no race)
- **Auto** — Remove a lock and let the solver pick freely
- Click any turn card to open a tooltip with all available races, their stats/SP, and which epithets they contribute to

### Settings

- **100+ character presets** with searchable dropdown (sets default aptitudes)
- Distance aptitudes (Sprint, Mile, Medium, Long) and surface aptitudes (Turf, Dirt), each A through G
- Minimum aptitude floor threshold
- Max consecutive races constraint
- Forced epithets with search/filter
- Advanced scoring weights: race bonus %, stat weight, SP weight, hint weight, epithet multiplier, 3-race penalty, race cost %

### Epithets

- Tracks all epithet categories: Dirt series, Classic/Senior titles, regional conquerors, Pro Racer, and more
- Shows which races contribute to in-progress epithets (colored dots on turn cards)
- Glowing dots indicate a race completes an epithet
- Click an epithet card to highlight all contributing races on the schedule

### Sharing

- Generate a share link encoding your full schedule state (settings, locks, freeze point)
- Import a schedule from a pasted link or code
- URL hash-based — no server required

### UI

- Light and dark themes (persists to localStorage, falls back to system preference)
- Responsive layout with collapsible settings drawer
- Top bar metrics: race count, epithet count, stats, SP, hints, total score, solver status
- Toast notifications for copy/import actions
- Keyboard support: Escape closes modals and tooltips

---

## Project Structure

```text
index.html            Main page shell and layout
app.js                UI logic, state management, tooltip/lock interactions, auto-solve
solver-browser.js     MILP model builder, GLPK solver interface, epithet tracking
styles.css            Full theme system (light + dark), responsive layout
races.json            Race database (grade, track, surface, distance, stats, SP)
epithets.json         Epithet definitions (name, conditions, rewards)
favicon.ico           Site icon
CNAME                 Custom domain config (race.daftuyda.moe)
```

---

## Deploying

This is a fully static site — no build step, no backend.

### GitHub Pages

1. Fork or clone this repository.
2. Go to **Settings > Pages**.
3. Under **Build and deployment**, choose **Deploy from a branch**.
4. Select your main branch and **/ (root)**.
5. Save. Your site will be live at `https://<username>.github.io/<repo>/`.

### Custom Domain

To use a custom domain, update the `CNAME` file with your domain and configure DNS to point to GitHub Pages. See [GitHub's custom domain docs](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site).

### Any Static Host

Upload all files to the root of any static hosting provider (Netlify, Vercel, Cloudflare Pages, etc.). No configuration needed — everything uses relative paths.

---

## Development Guide

### Prerequisites

No build tools are required. The project is plain HTML, CSS, and vanilla JavaScript (ES modules). To develop locally:

1. Clone the repo.
2. Serve the directory with any local HTTP server (ES modules require it):

   ```bash
   # Python
   python -m http.server 8000

   # Node
   npx serve .

   # VS Code
   # Use the "Live Server" extension
   ```

3. Open `http://localhost:8000` in your browser.

### Architecture Overview

The app follows a simple data flow:

```text
User interaction (click/change)
  -> state update (app.js)
    -> queueSolve() with debounce
      -> solveWithManualLocks() (solver-browser.js)
        -> GLPK MILP solver (WebAssembly)
      <- optimized schedule result
    <- applyPayload() updates state
  <- renderSchedule() redraws UI
```

**State** lives in a single `state` object in `app.js`. The solver is stateless — it receives settings and locks, returns a full schedule.

**Locks** are stored as `{ index: raceName | '[No race]' }`. The freeze-before-index mechanism auto-locks all turns before the latest explicit lock to their current solver output.

### Key Conventions

- **No build step.** All code is shipped as-is. No transpilation, bundling, or minification.
- **No frameworks.** The UI is vanilla DOM manipulation. Keep it that way.
- **Single-file modules.** `app.js` owns the UI, `solver-browser.js` owns optimization. Don't split these further without good reason.
- **CSS variables for theming.** All colors go through CSS custom properties defined at the top of `styles.css`. Never hardcode colors in component styles.
- **Debounced solving.** Settings changes use 250ms debounce; lock/skip actions solve immediately (0ms). Follow this pattern for new interactions.

### Modifying Race / Epithet Data

- **races.json** — Each entry has: `name`, `grade`, `track`, `surface`, `distance`, `year`, `half`, `month`. Add new races here.
- **epithets.json** — Each entry has: `name`, `condition` (display text), `reward` (display text), `stat_value`, `hint`, and race matching criteria. The solver uses `epithetRacePredicates()` in `solver-browser.js` to build matching functions from these.

When adding new data, verify the solver still returns OPTIMAL for default settings before committing.

### Adding New Constraints

New solver constraints go in `optimizeSchedule()` in `solver-browser.js`. Follow the existing pattern:

1. Define variables if needed (`glpk.GLP_BV` for binary).
2. Add constraints with `addConstraint()`.
3. Add objective terms with `addObjectiveTerm()`.
4. Test with forced epithets enabled to ensure the model stays feasible.

### External Dependencies

| Dependency | Version | Loaded From | Purpose |
| --- | --- | --- | --- |
| [GLPK.js](https://github.com/jvail/glpk.js) | 5.0.0 | jsDelivr CDN | MILP solver (WebAssembly) |
| [M PLUS Rounded 1c](https://fonts.google.com/specimen/M+PLUS+Rounded+1c) | — | Google Fonts | UI typeface |
| [Outfit](https://fonts.google.com/specimen/Outfit) | — | Google Fonts | Body typeface |

No npm packages. No node_modules. If you need to vendor GLPK for offline use, download the dist files and update the import path in `solver-browser.js`.

---

## License

This project does not currently include a license. Contact the repository owner for usage terms.
