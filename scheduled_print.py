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
#
# Usage:
#   SCHEDULE_PRINT FILE=my_part.gcode DELAY=90m
#   SCHEDULE_PRINT FILE=my_part.gcode DELAY=5400
#   SCHEDULE_PRINT FILE=my_part.gcode AT=22:30
#   SCHEDULE_PRINT FILE=my_part.gcode AT="2026-08-26 06:00"
#   SCHEDULE_PRINT_CANCEL
#   QUERY_SCHEDULED_PRINT
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

import logging
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

    # ---------------- command handlers ----------------

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

        sdcard = self.printer.lookup_object('virtual_sdcard')
        known_files = [f[0] if isinstance(f, (tuple, list)) else f
                       for f in sdcard.get_file_list()]
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

    # ---------------- internals ----------------

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
