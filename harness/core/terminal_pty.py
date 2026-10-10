"""
Interactive PTY / SSH Shell Bridge over WebSocket for Roaming Continuity.
Spawns interactive shell processes (PowerShell, bash, or persistent remote tmux over SSH)
and pipes stdin/stdout duplex over WebSockets with ANSI color support.
Uses genuine POSIX pseudoterminals (pty.openpty) on Linux for 100% terminal compatibility.
"""

import os
import sys
import json
import asyncio
import logging
import subprocess
import threading
from typing import Optional, Callable

logger = logging.getLogger("Harness.PTY")

# Try importing Linux PTY modules
HAS_PTY = False
if sys.platform != "win32":
    try:
        import pty
        import termios
        import fcntl
        import struct
        HAS_PTY = True
    except ImportError:
        HAS_PTY = False


class TerminalSession:
    def __init__(self, session_id: str, on_output: Callable[[str], None], target_host: Optional[str] = None):
        self.session_id = session_id
        self.on_output = on_output
        self.target_host = target_host
        self.process: Optional[subprocess.Popen] = None
        self.master_fd: Optional[int] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._scrollback: list[str] = []
        self._max_scrollback_chars: int = 300_000

    def _emit(self, text: str):
        if not text:
            return
        self._scrollback.append(text)
        total = sum(len(c) for c in self._scrollback)
        while total > self._max_scrollback_chars and len(self._scrollback) > 1:
            total -= len(self._scrollback.pop(0))
        if self.on_output:
            try:
                self.on_output(text)
            except Exception as e:
                logger.debug(f"Error calling on_output: {e}")

    def get_scrollback(self) -> str:
        return "".join(self._scrollback)

    def start(self):
        """Starts the interactive shell subprocess using real PTY when available."""
        if self._running:
            return

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"

        if HAS_PTY and sys.platform != "win32":
            try:
                self.master_fd, slave_fd = pty.openpty()

                # Set standard 24x80 initial size
                try:
                    winsize = struct.pack("HHHH", 24, 80, 0, 0)
                    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
                except Exception:
                    pass

                # Target selection
                if self.target_host and self.target_host not in ("127.0.0.1", "localhost"):
                    from harness.config import fleet_config   # host keys per config (accept-new by default)
                    cmd = ["ssh", "-tt"] + fleet_config.ssh_options(batch=False) + [f"root@{self.target_host}"]
                else:
                    # Interactive login bash on local system
                    cmd = ["bash", "-l"]

                self.process = subprocess.Popen(
                    cmd,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    env=env,
                    preexec_fn=os.setsid,
                    close_fds=True
                )
                os.close(slave_fd)
                self._running = True

                self._reader_thread = threading.Thread(target=self._read_pty_master, daemon=True)
                self._reader_thread.start()

                logger.info(f"⚡ Started true PTY terminal session {self.session_id} on master_fd={self.master_fd}")
                return
            except Exception as e:
                logger.error(f"Failed to spawn POSIX PTY process: {e}")
                self._emit(f"\r\n[ERROR: Failed to launch POSIX PTY: {e}]\r\n")

        # Fallback for Windows or non-pty environments
        try:
            if sys.platform == "win32":
                cmd = ["powershell.exe", "-NoLogo", "-NoExit", "-Command", "-"]
            else:
                cmd = ["bash", "-i"]

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            self._running = True
            self._reader_thread = threading.Thread(target=self._read_pipe_stdout, daemon=True)
            self._reader_thread.start()

            logger.info(f"Started pipe fallback terminal session {self.session_id}")
            self._emit(f"\r\n[STONESAGE PTY] Connected to {cmd[0]} (Fallback Pipe)\r\n")
        except Exception as e:
            logger.error(f"Failed to spawn fallback terminal process: {e}")
            self._emit(f"\r\n[ERROR: Failed to launch shell: {e}]\r\n")

    def _read_pty_master(self):
        """Reads output bytes from POSIX PTY master file descriptor."""
        while self._running and self.master_fd is not None:
            try:
                data = os.read(self.master_fd, 1024)
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                if text:
                    self._emit(text)
            except OSError:
                # EIO or similar on terminal close
                break
            except Exception as ex:
                logger.debug(f"PTY read exception: {ex}")
                break
        self._running = False
        self._emit("\r\n[STONESAGE PTY] Interactive session disconnected.\r\n")

    def _read_pipe_stdout(self):
        """Reads output bytes from standard pipe fallback."""
        while self._running and self.process and self.process.stdout:
            try:
                raw_chunk = self.process.stdout.read(1024)
                if not raw_chunk:
                    break
                text = raw_chunk.decode("utf-8", errors="replace")
                if text:
                    self._emit(text)
            except Exception:
                break
        self._running = False
        self._emit("\r\n[STONESAGE PTY] Pipe process terminated.\r\n")

    def write_input(self, data: str):
        """Writes user input keystrokes into the process PTY or stdin pipe."""
        if not self._running:
            return
        if self.master_fd is not None:
            try:
                os.write(self.master_fd, data.encode("utf-8", errors="replace"))
            except Exception as e:
                logger.warning(f"Error writing to PTY master_fd: {e}")
            return

        if self.process and self.process.stdin:
            try:
                self.process.stdin.write(data.encode("utf-8", errors="replace"))
                self.process.stdin.flush()
            except Exception as e:
                logger.warning(f"Error writing to pipe stdin: {e}")

    def resize(self, rows: int, cols: int):
        """Resizes the pseudoterminal window."""
        if self.master_fd is not None and HAS_PTY:
            try:
                winsize = struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except Exception as e:
                logger.debug(f"Error resizing PTY: {e}")

    def terminate(self):
        """Terminates the shell subprocess and cleans up file descriptors."""
        self._running = False
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except Exception:
                pass
            self.master_fd = None

        if self.process:
            try:
                if self.process.stdin:
                    try:
                        self.process.stdin.close()
                    except Exception:
                        pass
                if self.process.stdout:
                    try:
                        self.process.stdout.close()
                    except Exception:
                        pass
                self.process.terminate()
                try:
                    self.process.wait(timeout=1.0)
                except Exception:
                    self.process.kill()
            except Exception:
                pass
            self.process = None


class PTYManager:
    def __init__(self):
        self.sessions: dict[str, TerminalSession] = {}

    def get_or_create_session(self, session_id: str, on_output: Callable[[str], None], target_host: Optional[str] = None) -> TerminalSession:
        if session_id not in self.sessions or not self.sessions[session_id]._running:
            session = TerminalSession(session_id=session_id, on_output=on_output, target_host=target_host)
            session.start()
            self.sessions[session_id] = session
        else:
            # Update output callback on reconnect
            self.sessions[session_id].on_output = on_output
        return self.sessions[session_id]

    def resize_session(self, session_id: str, rows: int, cols: int):
        if session_id in self.sessions:
            self.sessions[session_id].resize(rows=rows, cols=cols)

    def close_session(self, session_id: str):
        if session_id in self.sessions:
            self.sessions[session_id].terminate()
            del self.sessions[session_id]


pty_manager = PTYManager()
