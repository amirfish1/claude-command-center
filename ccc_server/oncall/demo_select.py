"""A one-key scene picker; restore the terminal even on interruption."""
import sys


def pick():
    choices = {'1': 'customer', '2': 'ci', '3': 'monitoring', '4': 'all'}
    if not sys.stdin.isatty():
        return 'customer'
    import termios
    import tty
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            key = sys.stdin.read(1)
            if key in choices:
                print(key)
                return choices[key]
            if key in ('\r', '\n', ''):
                return 'customer'
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
