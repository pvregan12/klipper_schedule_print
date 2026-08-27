# scheduled_print.py -- Klipper extra to schedule a print for a future
# time or after a delay.
#
# Install:
#   1. Copy this file to ~/klipper/klippy/extras/scheduled_print.py
#   2. Add a [scheduled_print] section to printer.cfg (see below)
#   3. RESTART (or FIRMWARE_RESTART) Klipper
#
# Example printer.cfg section (all options optional, defaults shown):
#
#   [scheduled_print]
#   home_gcode: G28
#   clean_nozzle_gcode: CLEAN_NOZZLE
#   print_start_gcode: PRINT_START
#   menu_max_files: 12
#   menu_presets: Now=2s,+1 hour=1h,+3 hours=3h,+6 hours=6h,Tonight 10pm=22:00,Tomorrow 6am=06:00
#
# Usage:
#   SCHEDULE_PRINT FILE=my_part.gcode DELAY=90m
#   SCHEDULE_PRINT FILE=my_part.gcode DELAY=5400
#   SCHEDULE_PRINT FILE=my_part.gcode AT=22:30
#   SCHEDULE_PRINT FILE=my_part.gcode AT="2026-08-26 06:00"
#   SCHEDULE_PRINT_CANCEL
#   QUERY_SCHEDULED_PRINT
#
#   SCHEDULE_PRINT_MENU
#     Pops up a Mainsail/Fluidd prompt listing recent SD card files as
#     buttons. Tapping one opens a second prompt with fixed time
#     presets (from menu_presets); tapping a preset calls SCHEDULE_PRINT
#     for you. No typing required.
#
# When the scheduled time arrives, the module runs, in order:
#   home_gcode -> clean_nozzle_gcode -> print_start_gcode -> the print file
#
# Caveats:
#   - The schedule lives in memory only. A Klipper restart (RESTART,
#     FIRMWARE_RESTART, or a power cycle) clears any pending schedule,
#     the same as delayed_gcode and regular macros.
#   - If your PRINT_START macro already homes / cleans the nozzle
#     itself, you'll get that work done twice. Either blank out
#     home_gcode/clean_nozzle_gcode in the config, or adjust PRINT_START
#     to skip steps it's told are already done.
#   - print_start_gcode is called with no parameters. If your PRINT_START
#     macro requires BED_TEMP/EXTRUDER_TEMP (or similar) arguments,
#     either give it defaults or set print_start_gcode in the config to
#     include them, e.g.:
#       print_start_gcode: PRINT_START BED_TEMP=60 EXTRUDER_TEMP=210
#   - SCHEDULE_PRINT_MENU relies on the "prompt" support built into
#     Mainsail/Fluidd (Klipper's [respond] module must be enabled --
#     it is by default). Filenames containing a literal double-quote
#     character or a pipe (|) character will have those characters
#     stripped when displayed/used, since neither the gcode parser nor
#     the prompt protocol has an escape sequence for them.

import logging
import os
import re
from datetime import datetime, timedelta


class ScheduledPrint:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')

        self.home_gcode = config.get('home_gcode', 'G28')
        self.clean_nozzle_gcode = config.get('clean_nozzle_gcode',
                                              'CLEAN_NOZZLE')
        self.print_start_gcode = config.get('print_start_gcode',
                                             'PRINT_START')

        self.menu_max_files = config.getint('menu_max_files', 12, minval=1)
        self.menu_presets_raw = config.get(
            'menu_presets',
            'Now=2s,+1 hour=1h,+3 hours=3h,+6 hours=6h,'
            'Tonight 10pm=22:00,Tomorrow 6am=06:00')

        self.timer = None
        self.scheduled_filename = None
        self.scheduled_time = None  # wall-clock datetime, for reporting

        self.gcode.register_command(
            'SCHEDULE_PRINT', self.cmd_SCHEDULE_PRINT,
            desc=self.cmd_SCHEDULE_PRINT_help)
        self.gcode.register_command(
            'SCHEDULE_PRINT_CANCEL', self.cmd_SCHEDULE_PRINT_CANCEL,
            desc=self.cmd_SCHEDULE_PRINT_CANCEL_help)
        self.gcode.register_command(
            'QUERY_SCHEDULED_PRINT', self.cmd_QUERY_SCHEDULED_PRINT,
            desc=self.cmd_QUERY_SCHEDULED_PRINT_help)
        self.gcode.register_command(
            'SCHEDULE_PRINT_MENU', self.cmd_SCHEDULE_PRINT_MENU,
            desc=self.cmd_SCHEDULE_PRINT_MENU_help)
        self.gcode.register_command(
            'SCHEDULE_PRINT_MENU_TIME', self.cmd_SCHEDULE_PRINT_MENU_TIME,
            desc=self.cmd_SCHEDULE_PRINT_MENU_TIME_help)

    # ---------------- time parsing helpers ----------------

    _DELAY_RE = re.compile(
        r'^\s*(?:(?P<hours>\d+(?:\.\d+)?)h)?'
        r'\s*(?:(?P<minutes>\d+(?:\.\d+)?)m)?'
        r'\s*(?:(?P<seconds>\d+(?:\.\d+)?)s)?\s*$',
        re.IGNORECASE)

    def _parse_delay(self, delay_str):
        """Accepts plain seconds ('5400') or a duration like '1h30m',
        '90m', '45s'. Returns seconds as a float."""
        delay_str = delay_str.strip()
        if re.fullmatch(r'\d+(\.\d+)?', delay_str):
            return float(delay_str)
        m = self._DELAY_RE.match(delay_str)
        if not m or not any(m.groups()):
            raise self.gcode.error(
                "Could not parse DELAY value '%s'. Use seconds (e.g. "
                "5400) or a duration like 1h30m, 90m, 45s." % (delay_str,))
        hours = float(m.group('hours') or 0)
        minutes = float(m.group('minutes') or 0)
        seconds = float(m.group('seconds') or 0)
        total = hours * 3600 + minutes * 60 + seconds
        if total <= 0:
            raise self.gcode.error("DELAY must be greater than zero")
        return total

    def _parse_at(self, at_str):
        """Accepts 'HH:MM', 'HH:MM:SS', 'YYYY-MM-DD HH:MM', or
        'YYYY-MM-DD HH:MM:SS'. Returns a wall-clock datetime. A
        time-only value rolls over to tomorrow if that time has
        already passed today."""
        at_str = at_str.strip()
        now = datetime.now()

        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
            try:
                return datetime.strptime(at_str, fmt)
            except ValueError:
                pass

        for fmt in ('%H:%M:%S', '%H:%M'):
            try:
                parsed = datetime.strptime(at_str, fmt)
            except ValueError:
                continue
            candidate = now.replace(hour=parsed.hour, minute=parsed.minute,
                                     second=parsed.second, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate

        raise self.gcode.error(
            "Could not parse AT value '%s'. Use HH:MM, HH:MM:SS, "
            "'YYYY-MM-DD HH:MM', or 'YYYY-MM-DD HH:MM:SS'." % (at_str,))

    # ---------------- command handlers: core scheduling ----------------

    cmd_SCHEDULE_PRINT_help = (
        "Schedule a print: SCHEDULE_PRINT FILE=<name> DELAY=<duration> "
        "  or  SCHEDULE_PRINT FILE=<name> AT=<time>")

    def cmd_SCHEDULE_PRINT(self, gcmd):
        filename = gcmd.get('FILE')
        delay_str = gcmd.get('DELAY', None)
        at_str = gcmd.get('AT', None)

        if (delay_str is None) == (at_str is None):
            raise gcmd.error(
                "SCHEDULE_PRINT requires exactly one of DELAY= or AT=")

        known_files = self._get_all_files()
        if filename not in known_files:
            raise gcmd.error(
                "File '%s' not found on the virtual SD card. Check the "
                "name/path and case (list files with the standard "
                "GET_FILE_LIST / M20)." % (filename,))

        if delay_str is not None:
            delay = self._parse_delay(delay_str)
            run_at_wall = datetime.now() + timedelta(seconds=delay)
        else:
            run_at_wall = self._parse_at(at_str)
            delay = (run_at_wall - datetime.now()).total_seconds()
            if delay <= 0:
                raise gcmd.error("AT= time must be in the future")

        self._cancel_existing()

        waketime = self.reactor.monotonic() + delay
        self.scheduled_filename = filename
        self.scheduled_time = run_at_wall
        self.timer = self.reactor.register_timer(self._on_timer, waketime)

        gcmd.respond_info(
            "Print '%s' scheduled for %s (in %s)" % (
                filename, run_at_wall.strftime('%Y-%m-%d %H:%M:%S'),
                self._format_delay(delay)))

    cmd_SCHEDULE_PRINT_CANCEL_help = "Cancel a pending scheduled print"

    def cmd_SCHEDULE_PRINT_CANCEL(self, gcmd):
        if self.timer is None:
            gcmd.respond_info("No scheduled print to cancel")
            return
        cancelled_file = self.scheduled_filename
        self._cancel_existing()
        gcmd.respond_info("Cancelled scheduled print '%s'" % (cancelled_file,))

    cmd_QUERY_SCHEDULED_PRINT_help = "Report the pending scheduled print, if any"

    def cmd_QUERY_SCHEDULED_PRINT(self, gcmd):
        if self.timer is None:
            gcmd.respond_info("No print currently scheduled")
            return
        remaining = max((self.scheduled_time - datetime.now()).total_seconds(), 0)
        gcmd.respond_info(
            "Print '%s' scheduled for %s (in %s)" % (
                self.scheduled_filename,
                self.scheduled_time.strftime('%Y-%m-%d %H:%M:%S'),
                self._format_delay(remaining)))

    # ---------------- command handlers: click-to-schedule menu ----------------

    cmd_SCHEDULE_PRINT_MENU_help = (
        "Show a Mainsail/Fluidd prompt to pick a file to schedule")

    def cmd_SCHEDULE_PRINT_MENU(self, gcmd):
        files = self._get_recent_files(self.menu_max_files)
        if not files:
            gcmd.respond_info("No files found on the SD card")
            return

        lines = [
            '// action:prompt_begin Schedule a Print',
            '// action:prompt_text Choose a file (most recent first):',
        ]
        for fname in files:
            label = self._display_label(fname)
            command = 'SCHEDULE_PRINT_MENU_TIME FILE=%s' % (
                self._quote(fname),)
            lines.append('// action:prompt_button %s|%s' % (label, command))
        lines.append(
            '// action:prompt_footer_button Cancel|RESPOND '
            'MSG="Schedule cancelled"|secondary')
        lines.append('// action:prompt_show')
        for line in lines:
            self.gcode.respond_raw(line)

    cmd_SCHEDULE_PRINT_MENU_TIME_help = (
        "Show a prompt to pick when the given FILE should print "
        "(called by SCHEDULE_PRINT_MENU)")

    def cmd_SCHEDULE_PRINT_MENU_TIME(self, gcmd):
        filename = gcmd.get('FILE')
        presets = self._parse_menu_presets()
        if not presets:
            raise gcmd.error(
                "menu_presets in [scheduled_print] has no valid entries")

        lines = [
            '// action:prompt_begin Schedule Print',
            '// action:prompt_text When should %s start?' % (
                self._display_label(filename),),
        ]
        for label, param in presets:
            command = 'SCHEDULE_PRINT FILE=%s %s' % (
                self._quote(filename), param)
            lines.append('// action:prompt_button %s|%s' % (label, command))
        lines.append(
            '// action:prompt_footer_button Cancel|RESPOND '
            'MSG="Schedule cancelled"|secondary')
        lines.append('// action:prompt_show')
        for line in lines:
            self.gcode.respond_raw(line)

    def _parse_menu_presets(self):
        presets = []
        for item in self.menu_presets_raw.split(','):
            item = item.strip()
            if not item:
                continue
            if '=' not in item:
                logging.warning(
                    "scheduled_print: ignoring malformed menu_presets "
                    "entry '%s' (expected Label=Value)", item)
                continue
            label, value = item.split('=', 1)
            label = label.strip()
            value = value.strip()
            if not label or not value:
                continue
            if ':' in value:
                param = 'AT=%s' % (self._quote(value),)
            else:
                param = 'DELAY=%s' % (self._quote(value),)
            presets.append((label, param))
        return presets

    # ---------------- internals ----------------

    def _get_all_files(self):
        sdcard = self.printer.lookup_object('virtual_sdcard')
        return [f[0] if isinstance(f, (tuple, list)) else f
                for f in sdcard.get_file_list()]

    def _get_recent_files(self, limit):
        sdcard = self.printer.lookup_object('virtual_sdcard')
        files = self._get_all_files()
        try:
            sdcard_dir = sdcard.sdcard_dirname
            files.sort(
                key=lambda fn: os.path.getmtime(os.path.join(sdcard_dir, fn)),
                reverse=True)
        except Exception:
            logging.exception(
                "scheduled_print: could not sort files by modified time, "
                "falling back to alphabetical order")
        return files[:limit]

    @staticmethod
    def _display_label(value, max_len=40):
        # Buttons/text use '|' as a field separator and have no escape
        # for it, so strip it out of anything we display.
        clean = value.replace('|', '')
        if len(clean) > max_len:
            clean = clean[:max_len - 3] + '...'
        return clean

    @staticmethod
    def _quote(value):
        # Wrap a value for use as a quoted gcode parameter. Klipper's
        # parser has no escape sequence for a literal double quote, so
        # strip any that appear rather than risk a broken command.
        return '"%s"' % (value.replace('"', "'").replace('|', ''),)

    def _cancel_existing(self):
        if self.timer is not None:
            self.reactor.unregister_timer(self.timer)
        self.timer = None
        self.scheduled_filename = None
        self.scheduled_time = None

    def _on_timer(self, eventtime):
        filename = self.scheduled_filename
        self.timer = None
        self.scheduled_filename = None
        self.scheduled_time = None

        logging.info("scheduled_print: launching scheduled print '%s'", filename)
        try:
            self._run_startup_sequence(filename)
        except Exception:
            logging.exception("scheduled_print: failed to start scheduled print")
            self.gcode.respond_info(
                "scheduled_print: failed to start '%s' -- see klippy.log"
                % (filename,))
        return self.reactor.NEVER

    def _run_startup_sequence(self, filename):
        for step in (self.home_gcode, self.clean_nozzle_gcode,
                     self.print_start_gcode):
            if step:
                self.gcode.run_script_from_command(step)
        self.gcode.run_script_from_command(
            'SDCARD_PRINT_FILE FILENAME="%s"' % (filename,))

    @staticmethod
    def _format_delay(seconds):
        seconds = int(round(seconds))
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        parts = []
        if hours:
            parts.append('%dh' % hours)
        if minutes:
            parts.append('%dm' % minutes)
        if secs or not parts:
            parts.append('%ds' % secs)
        return ' '.join(parts)


def load_config(config):
    return ScheduledPrint(config)
