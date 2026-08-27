# scheduled_print

A Klipper extra that lets you schedule a print for a future time or
after a delay, running a configurable warm-up sequence
(home &rarr; clean nozzle &rarr; PRINT_START) before it starts.

## Install

```bash
cd ~
git clone https://github.com/<your-username>/scheduled_print.git
cd scheduled_print
./install.sh
```

By default the installer looks for Klipper at `~/klipper`. If yours
lives elsewhere:

```bash
KLIPPER_PATH=/home/pi/klipper ./install.sh
```

The installer symlinks `scheduled_print.py` into
`klippy/extras/scheduled_print.py`, so a `git pull` in this repo
later is picked up automatically — no need to re-run the installer
for updates (a Klipper restart is still needed to load the new code).

## Configuration

Add to `printer.cfg`:

```ini
[scheduled_print]
#home_gcode: G28
#clean_nozzle_gcode: CLEAN_NOZZLE
#print_start_gcode: PRINT_START
#menu_max_files: 12
#menu_presets: Now=2s,+1 hour=1h,+3 hours=3h,+6 hours=6h,Tonight 10pm=22:00,Tomorrow 6am=06:00
```

All options are optional; the values above are the defaults.
Set `home_gcode`/`clean_nozzle_gcode` to an empty string to skip
that step, e.g. if your `PRINT_START` macro already handles homing
and nozzle cleaning:

```ini
[scheduled_print]
home_gcode:
clean_nozzle_gcode:
```

`menu_max_files` caps how many files `SCHEDULE_PRINT_MENU` lists
(most recently modified first). `menu_presets` is a comma-separated
list of `Label=Value` pairs used by `SCHEDULE_PRINT_MENU_TIME` --
each `Value` is treated as a clock time (`AT=`) if it contains a
`:`, otherwise as a duration (`DELAY=`).

## Usage

### Command line / console

```
SCHEDULE_PRINT FILE=my_part.gcode DELAY=90m
SCHEDULE_PRINT FILE=my_part.gcode DELAY=5400
SCHEDULE_PRINT FILE=my_part.gcode AT=22:30
SCHEDULE_PRINT FILE=my_part.gcode AT="2026-08-26 06:00"
SCHEDULE_PRINT_CANCEL
QUERY_SCHEDULED_PRINT
```

`FILE` must be a path already present on the virtual SD card (the
same files `SDCARD_PRINT_FILE` would accept).

`DELAY` accepts plain seconds or a duration like `1h30m`, `90m`,
`45s`.

`AT` accepts `HH:MM`, `HH:MM:SS`, `YYYY-MM-DD HH:MM`, or
`YYYY-MM-DD HH:MM:SS`. A time-only value rolls over to tomorrow if
that time has already passed today.

When the scheduled time arrives, `home_gcode`, then
`clean_nozzle_gcode`, then `print_start_gcode` are run in order,
followed by `SDCARD_PRINT_FILE FILENAME=<file>`.

### Click-to-schedule menu (Mainsail / Fluidd)

Run `SCHEDULE_PRINT_MENU` (from the console, or add it as a macro
button in Mainsail's UI under Interface Settings &rarr; Macros) to
get a tap-through picker instead of typing a filename:

1. A prompt lists your most recent SD card files as buttons.
2. Tapping one opens a second prompt with the time presets from
   `menu_presets`.
3. Tapping a preset calls `SCHEDULE_PRINT` for you.

This relies on Klipper's built-in "prompt" protocol, which needs
the `[respond]` module enabled (it is enabled by default; add an
empty `[respond]` section to `printer.cfg` if yours doesn't have
one already). Filenames or preset labels containing a literal `"`
or `|` character have that character stripped, since neither the
gcode parser nor the prompt protocol has a way to escape it.

## Caveats

- The schedule is held in memory only. `RESTART`, `FIRMWARE_RESTART`,
  or a power cycle clears any pending schedule — same as
  `delayed_gcode` and regular macros.
- `print_start_gcode` is called with no arguments. If your
  `PRINT_START` macro requires parameters (e.g. `BED_TEMP=`,
  `EXTRUDER_TEMP=`), either give it defaults or bake the values into
  the config line, e.g.
  `print_start_gcode: PRINT_START BED_TEMP=60 EXTRUDER_TEMP=210`.
- If `PRINT_START` already homes/cleans the nozzle itself, leaving
  the defaults in place means that work happens twice.

## Uninstall

```bash
cd ~/scheduled_print
./uninstall.sh
```

## Auto-updates via Moonraker (optional)

To let Mainsail/Fluidd track and update this repo the same way they
track Klipper itself, add to `moonraker.conf`:

```ini
[update_manager scheduled_print]
type: git_repo
path: ~/scheduled_print
origin: https://github.com/<your-username>/scheduled_print.git
primary_branch: main
is_system_service: False
```

Then restart Moonraker. It will show up as an updatable entry in the
web UI, and a Moonraker-driven "update" is just a `git pull` in
`~/scheduled_print` — the symlink means Klipper picks it up on its
next restart with no extra steps.

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
