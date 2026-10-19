import time
import socket
import logging
import requests
import threading
from enum import Enum
from dataclasses import dataclass
from typing import Callable, Optional


class NetworkStatus(Enum):
    ONLINE   = "online"
    OFFLINE  = "offline"


@dataclass
class ConnectionState:
    iqoption:        bool = False
    telegram:        bool = False
    telegram_source: bool = False


class NetworkMonitor:
    def __init__(self,
                 check_interval: float = 5.0,
                 ping_targets: list = None,
                 recovery_callback: Optional[Callable] = None,
                 disconnect_callback: Optional[Callable] = None,
                 reconnect_callbacks: Optional[dict] = None):

        self.log             = logging.getLogger("NetworkMonitor")
        self.check_interval  = check_interval
        self.ping_targets    = ping_targets or ["8.8.8.8", "1.1.1.1"]

        # Legacy callbacks (kept for API compatibility)
        self.recovery_callback   = recovery_callback
        self.disconnect_callback = disconnect_callback
        self.reconnect_callbacks = reconnect_callbacks or {}

        self.running              = False
        self.monitor_thread       = None
        self.current_status       = NetworkStatus.ONLINE
        self.consecutive_failures = 0
        self.connection_state     = ConnectionState()
        self._lock                = threading.Lock()

        # Reconnect runs in its own thread — NEVER blocks the monitor loop
        self._reconnect_thread: Optional[threading.Thread] = None

    # ──────────────────────────────────────────────────────────────────
    #  Public API
    # ──────────────────────────────────────────────────────────────────

    def start(self):
        if self.monitor_thread and self.monitor_thread.is_alive():
            return
        self.running        = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop, name="NetworkMonitor", daemon=True
        )
        self.monitor_thread.start()
        self.log.info("Network monitor started (interval: %.1fs)", self.check_interval)

    def stop(self):
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
        self.log.info("Network monitor stopped")

    def is_online(self) -> bool:
        return self.current_status == NetworkStatus.ONLINE

    def update_connection_state(self, service: str, is_connected: bool):
        with self._lock:
            if service == "iqoption":
                self.connection_state.iqoption = is_connected
            elif service == "telegram":
                self.connection_state.telegram = is_connected
            elif service == "telegram_source":
                self.connection_state.telegram_source = is_connected

    # ──────────────────────────────────────────────────────────────────
    #  Monitor loop  — returns immediately every iteration, never blocks
    # ──────────────────────────────────────────────────────────────────

    def _monitor_loop(self):
        while self.running:
            is_online = self._check_internet()

            if is_online:
                self.consecutive_failures = 0

                if self.current_status != NetworkStatus.ONLINE:
                    self.current_status = NetworkStatus.ONLINE
                    self.log.info("✅ Internet restored — scheduling reconnect")
                    self._schedule_reconnect()   # spawns a thread, returns instantly

            else:
                self.consecutive_failures += 1
                self.log.warning(
                    "Network check failed (%d)", self.consecutive_failures
                )

                if self.current_status != NetworkStatus.OFFLINE:
                    self.current_status = NetworkStatus.OFFLINE
                    self.disconnect_callback()
                    self._disconnect_services()  # fast, no blocking calls inside

            time.sleep(self.check_interval)

    # ──────────────────────────────────────────────────────────────────
    #  Reconnect worker  — always runs in its OWN thread
    # ──────────────────────────────────────────────────────────────────

    def _schedule_reconnect(self):
        """Spawn a fresh reconnect worker.
        Captures the PREVIOUS thread reference before overwriting self._reconnect_thread
        so the new worker can join the old one without joining itself."""

        # Snapshot the previous worker BEFORE we overwrite the reference
        previous_thread = self._reconnect_thread

        def _run_when_ready():
            # Wait for the previous worker to finish (different thread object)
            if previous_thread and previous_thread.is_alive():
                self.log.info("Waiting for previous reconnect worker to finish...")
                previous_thread.join(timeout=5)
                if previous_thread.is_alive():
                    self.log.warning("Previous worker still alive after 120s — proceeding anyway")

            # Only reconnect if network is still up
            if self.current_status == NetworkStatus.ONLINE:
                self._reconnect_services()
            else:
                self.log.info("Network dropped again before reconnect ran — skipping")

        self._reconnect_thread = threading.Thread(
            target=_run_when_ready,
            name="NetworkReconnect",
            daemon=True,
        )
        self._reconnect_thread.start()

    def _reconnect_services(self):
        """All heavy reconnect logic lives here, in a background thread."""
        try:
            self.log.info("🔌 Network restored! Reconnecting services...")

            # 1. Reconnect Telegram first
            tg_cb = self.reconnect_callbacks.get("telegram")
            if tg_cb:
                self.log.info("📡 Reconnecting Telegram...")
                try:
                    tg_cb()
                    time.sleep(3)   # give Telegram time to stabilise
                except Exception as e:
                    self.log.error("Telegram reconnection error: %s", e)

            # 2. Wait for DNS before touching IQ Option
            iq_cb = self.reconnect_callbacks.get("iqoption")
            if iq_cb:
                self.log.info("💰 Reconnecting IQ Option...")
                self._wait_for_dns()
                try:
                    iq_cb()
                except Exception as e:
                    self.log.error("IQ Option reconnection error: %s", e)

            # 3. Generic recovery callback (legacy support)
            if self.recovery_callback:
                try:
                    self.recovery_callback()
                except Exception as e:
                    self.log.error("Recovery callback error: %s", e)

        except Exception as e:
            self.log.error("Unexpected error in reconnect worker: %s", e)

    # ──────────────────────────────────────────────────────────────────
    #  Disconnect — called exactly once per OFFLINE transition, fast
    # ──────────────────────────────────────────────────────────────────

    def _disconnect_services(self):
        self.log.info("🔌 Network lost — disconnecting services...")

        # Disconnect Telegram
        tg_cb = self.reconnect_callbacks.get("telegram_disconnect")
        if tg_cb:
            try:
                tg_cb()
            except Exception as e:
                self.log.error("Telegram disconnect error: %s", e)

        # # Disconnect IQ Option
        # iq_cb = self.reconnect_callbacks.get("iqoption_disconnect")
        # if iq_cb:
        #     try:
        #         iq_cb()
        #     except Exception as e:
        #         self.log.error("IQ Option disconnect error: %s", e)

        if self.disconnect_callback:
            try:
                self.disconnect_callback()
            except Exception as e:
                self.log.error("Disconnect callback error: %s", e)

    # ──────────────────────────────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────────────────────────────

    def _check_internet(self) -> bool:
        for target in self.ping_targets:
            try:
                socket.create_connection((target, 53), timeout=3)
                return True
            except (socket.timeout, socket.error):
                continue
        try:
            requests.get("https://www.google.com", timeout=5)
            return True
        except requests.RequestException:
            return False

    def _wait_for_dns(self, host: str = "api.iqoption.com", timeout: int = 30) -> bool:
        """Block inside the reconnect worker until DNS resolves."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                socket.getaddrinfo(host, 443)
                self.log.info("✅ DNS ready for %s", host)
                return True
            except socket.gaierror:
                time.sleep(1)
        self.log.warning("DNS timeout for %s — proceeding anyway", host)
        return False