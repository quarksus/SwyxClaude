"""Run `claude` in a pty, proxy the real terminal, and allow injecting keystrokes."""
import fcntl
import os
import select
import signal
import struct
import subprocess
import sys
import termios
import time
import tty


class PtyHost:
    def __init__(self, argv):
        self.argv = argv
        self.master = None
        self._lock = __import__("threading").Lock()

    def _resize(self, *_):
        if self.master is None or not sys.stdin.isatty():
            return
        size = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, size)

    def send(self, data: bytes):
        with self._lock:
            os.write(self.master, data)

    def type_text(self, text: str, submit: bool):
        """Type text into Claude's prompt; Enter is sent separately so it is not treated as a paste."""
        self.send(" ".join(text.split()).encode())
        if submit:
            time.sleep(0.4)
            self.send(b"\r")

    def run(self, on_ready=lambda: None) -> int:
        self.master, slave = os.openpty()
        self._resize()
        proc = subprocess.Popen(
            self.argv, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        os.close(slave)
        signal.signal(signal.SIGWINCH, self._resize)
        interactive = sys.stdin.isatty()
        old = termios.tcgetattr(sys.stdin.fileno()) if interactive else None
        if interactive:
            tty.setraw(sys.stdin.fileno())
        on_ready()
        try:
            while True:
                try:
                    r, _, _ = select.select([sys.stdin.fileno(), self.master], [], [], 0.2)
                except InterruptedError:
                    continue
                if self.master in r:
                    try:
                        data = os.read(self.master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    os.write(sys.stdout.fileno(), data)
                if sys.stdin.fileno() in r:
                    data = os.read(sys.stdin.fileno(), 4096)
                    if not data:
                        break
                    self.send(data)
                if proc.poll() is not None and self.master not in r:
                    break
        finally:
            if old:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)
        return proc.wait()
