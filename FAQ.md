# FAQ

## Which game version is supported?

Only the Global Steam version. The launcher uses Steam app ID `3224770` and the Global `master.mdb`.

## Why did the event helper not open?

Confirm that CarrotBlender is installed and sending packets to the configured host and port. The defaults
are `127.0.0.1:17229`. Also confirm that the selected browser and Selenium driver can start.

## Where are training logs and packet exports stored?

Training logs are stored under `%AppData%\Uma-Launcher-Global\training_logs`. Packet and race exports are
written beside the executable in `packets` and `races`. Each export type has its own preference toggle.

## Does the launcher translate the game?

No. Character, outfit, event, race, and skill data used by the helper comes from the installed Global
`master.mdb`. The launcher does not install or download translations.

## Does it use a VPN or send startup telemetry?

No. VPN automation and startup usage reporting were removed.

## Does the helper still block ads?

Yes for Chrome and Edge. A current domain list is downloaded and validated while building the executable,
then bundled as a static asset. There is no runtime blocklist request. Firefox keeps its existing behavior.

## Does auto-update work?

Yes. Public builds check the `y3nly/UmaLauncher` releases and only install the
`UmaLauncher-Global.exe` asset.

## What information does “Send error report” transmit?

Only use the button if you want to send the displayed traceback and the shown launcher/build identifiers to
the configured Discord webhook. Reports are never submitted automatically.
