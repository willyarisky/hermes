"""Background Daemon and Process Manager for AGY Auth Adapter & Proxy."""

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from agy_auth_adapter.utils import (
    DEFAULT_PROXY_HOST,
    get_default_proxy_port,
    get_hermes_home,
    safe_read_json,
    safe_write_json,
)

logger = logging.getLogger("agy_auth_adapter.daemon")


class DaemonManager:
    """Manages the lifecycle of the AGY background proxy daemon."""

    def __init__(
        self,
        hermes_home: Optional[Path] = None,
        default_port: Optional[int] = None,
        default_host: Optional[str] = None,
    ):
        self.hermes_home = hermes_home or get_hermes_home()
        self.pid_file = self.hermes_home / "agy_proxy.pid"
        self.logs_dir = self.hermes_home / "logs"
        self.log_file = self.logs_dir / "agy_proxy.log"
        self.default_port = default_port or get_default_proxy_port()
        self.default_host = default_host or DEFAULT_PROXY_HOST

    def is_healthy(self, host: Optional[str] = None, port: Optional[int] = None, timeout: float = 1.0) -> bool:
        """Checks if the proxy server is running and responding on /health."""
        target_host = host or self.default_host
        target_port = port or self.default_port
        url = f"http://{target_host}:{target_port}/health"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AGY-Daemon-HealthCheck"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("service") is not None
        except Exception:
            return False
        return False

    def probe_port(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        timeout: float = 1.0,
    ) -> str:
        """Reports who is listening on the proxy port.

        Returns "free" (nothing listening), "agy" (our proxy answered /health),
        or "foreign" (something else owns the port — a web server, another app).
        A foreign listener is the usual cause of HTML 404s coming back from the
        configured base_url instead of chat completions.
        """
        target_host = host or self.default_host
        target_port = port or self.default_port

        try:
            with socket.create_connection((target_host, target_port), timeout=timeout):
                pass
        except OSError:
            return "free"

        return "agy" if self.is_healthy(target_host, target_port, timeout=timeout) else "foreign"

    def get_running_pid(self) -> Optional[int]:
        """Reads the PID file and checks if the process is currently active."""
        if not self.pid_file.exists():
            return None

        try:
            pid_str = self.pid_file.read_text(encoding="utf-8").strip()
            if not pid_str.isdigit():
                return None
            pid = int(pid_str)

            # Check if process is alive
            if os.name == "nt":
                # Windows check
                cmd = f"tasklist /FI \"PID eq {pid}\" /NH"
                out = subprocess.check_output(cmd, shell=True).decode("utf-8", errors="ignore")
                if str(pid) in out:
                    return pid
            else:
                # Unix check
                os.kill(pid, 0)
                return pid
        except Exception:
            pass

        # Cleanup stale pid file
        try:
            self.pid_file.unlink()
        except Exception:
            pass
        return None

    def start(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        verbose: bool = False,
        timeout: float = 10.0,
    ) -> Tuple[bool, str]:
        """Starts the AGY proxy server as a detached background daemon."""
        target_host = host or self.default_host
        target_port = port or self.default_port

        # Check if already running
        existing_pid = self.get_running_pid()
        if existing_pid and self.is_healthy(target_host, target_port):
            return True, f"AGY Daemon is already running (PID: {existing_pid}, http://{target_host}:{target_port})"

        if self.probe_port(target_host, target_port) == "foreign":
            return False, (
                f"Port {target_port} on {target_host} is already in use by another service "
                f"(it answered, but not as the AGY proxy). Free the port, or run the bridge "
                f"elsewhere with: hermes agy daemon start --port <PORT> "
                f"(and hermes agy setup --port <PORT> so Hermes points at it)."
            )

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log_out = open(self.log_file, "a", encoding="utf-8")

        cmd = [
            sys.executable,
            "-m",
            "agy_auth_adapter.cli",
            "proxy",
            "--host",
            target_host,
            "--port",
            str(target_port),
        ]
        if verbose:
            cmd.append("--verbose")

        creationflags = 0
        kwargs: Dict[str, Any] = {
            "stdout": log_out,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
        }

        if sys.platform == "win32":
            # Detach process on Windows
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            # Detach process on Unix
            kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(cmd, **kwargs)
            pid = proc.pid
            self.pid_file.write_text(str(pid), encoding="utf-8")
        except Exception as e:
            return False, f"Failed to spawn background process: {e}"

        # Wait for server to become healthy
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.is_healthy(target_host, target_port):
                return True, f"AGY background daemon started successfully (PID: {pid}, http://{target_host}:{target_port})"
            time.sleep(0.3)

        return False, f"AGY daemon process started (PID: {pid}), but healthcheck timed out. Check logs at: {self.log_file}"

    def stop(self) -> Tuple[bool, str]:
        """Stops the running AGY background daemon."""
        pid = self.get_running_pid()
        if not pid:
            if self.pid_file.exists():
                self.pid_file.unlink()
            return True, "AGY background daemon is not running."

        try:
            if sys.platform == "win32":
                subprocess.run(
                    f"taskkill /F /T /PID {pid}",
                    shell=True,
                    capture_output=True,
                )
            else:
                os.kill(pid, signal.SIGTERM)
                # Give it a second to shutdown gracefully
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

            if self.pid_file.exists():
                self.pid_file.unlink()

            return True, f"AGY background daemon (PID: {pid}) stopped."
        except Exception as e:
            return False, f"Error stopping daemon (PID: {pid}): {e}"

    def restart(self, host: Optional[str] = None, port: Optional[int] = None) -> Tuple[bool, str]:
        """Restarts the background daemon."""
        self.stop()
        time.sleep(0.5)
        return self.start(host=host, port=port)

    def status(self, host: Optional[str] = None, port: Optional[int] = None) -> Dict[str, Any]:
        """Returns detailed status of the background daemon."""
        target_host = host or self.default_host
        target_port = port or self.default_port
        pid = self.get_running_pid()
        probe = self.probe_port(target_host, target_port)
        healthy = probe == "agy"

        return {
            "running": pid is not None and healthy,
            "pid": pid,
            "healthy": healthy,
            "port_conflict": probe == "foreign",
            "endpoint": f"http://{target_host}:{target_port}/v1",
            "pid_file": str(self.pid_file),
            "log_file": str(self.log_file),
        }

    def ensure_running(self, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        """Transparently ensures the background daemon is running (auto-start sidecar)."""
        target_host = host or self.default_host
        target_port = port or self.default_port

        if self.is_healthy(target_host, target_port):
            return True

        logger.info("AGY proxy daemon not detected. Auto-launching in background...")
        success, msg = self.start(host=target_host, port=target_port)
        logger.info(msg)
        return success
