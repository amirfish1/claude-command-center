"""Plain transcripts and an optional paced terminal presentation."""
import shutil
import textwrap
import time


class Output:
    def __init__(self, *, quiet=False, fast=False, colour=False):
        self.quiet, self.fast, self.colour = quiet, fast, colour
        self.lines, self.events = [], []
        self.started = time.monotonic()

    def __call__(self, text):
        self.write(text)

    def phone(self, text):
        width = max(24, min(66, shutil.get_terminal_size().columns - 6))
        rows = [wrapped for line in text.splitlines()
                for wrapped in (textwrap.wrap(line, width) or [''])]
        border = '+' + '-' * (width + 2) + '+'
        box = '\n'.join([border, '| ' + 'PHONE'.ljust(width) + ' |', border] +
                        ['| ' + row.ljust(width) + ' |' for row in rows] + [border])
        self.write(text, rendered=box)

    def write(self, text, rendered=None):
        if not self.fast:
            time.sleep(.75)
        self.lines.append(text)
        display = rendered or text
        if self.colour:
            display = '\033[36m' + display + '\033[0m' if rendered else '\033[1m' + display + '\033[0m'
        self.events.append([round(time.monotonic() - self.started, 3), 'o', display.replace('\n', '\r\n') + '\r\n'])
        if not self.quiet:
            print(display, flush=True)
