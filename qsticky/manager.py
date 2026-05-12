import asyncio
import logging
import os
import signal
from datetime import datetime
from typing import Optional

from .config import HealthStatus, Settings
from .gluetun import GluetunClient
from .health import HealthManager
from .qbittorrent import QBittorrentClient


class PortManager:
    def __init__(self):
        self.settings = Settings()
        self.logger = self._setup_logger()
        self.health_status = HealthStatus(healthy=True, last_check=datetime.now())
        self.health_manager = HealthManager(
            health_status=self.health_status,
            health_file=os.getenv('HEALTH_FILE', '/tmp/health_status.json'),
            logger=self.logger
        )
        self.qbit = QBittorrentClient(
            settings=self.settings,
            logger=self.logger,
            health_status=self.health_status
        )
        self.gluetun = GluetunClient(
            settings=self.settings,
            logger=self.logger
        )
        self.current_port: Optional[int] = None
        self.shutdown_event = asyncio.Event()
        self._first_run = True

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger("qsticky")
        logger.setLevel(getattr(logging, self.settings.log_level.upper()))
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    async def handle_port_change(self) -> None:
        try:
            new_port = await self.gluetun.get_forwarded_port()
            if not new_port:
                self.health_status.healthy = False
                return

            current_qbit_port = await self.qbit.get_current_port()
            if current_qbit_port is None:
                self.health_status.healthy = False
                return

            self.current_port = new_port
            self.health_status.healthy = True

            if current_qbit_port != new_port:
                self.logger.info(f"Port change needed: {current_qbit_port} -> {new_port}")
                if await self.qbit.update_port(new_port):
                    self.health_status.last_port_change = datetime.now()
                    verified_port = await self.qbit.get_current_port()
                    if verified_port == new_port:
                        self.logger.info(f"Successfully updated port to {new_port}")
                        self.current_port = new_port
                    else:
                        self.logger.error(
                            f"Port change verification failed - expected {new_port}, got {verified_port}"
                        )
                        self.health_status.healthy = False
                        self.health_status.last_error = "Port change verification failed"
            else:
                if self._first_run:
                    self.logger.info(f"Initial port check: {new_port} already set correctly")
                else:
                    self.logger.debug(f"Port {new_port} already set correctly")
                self.current_port = current_qbit_port

            await self.health_manager.update_health_file(self.current_port)
            self._first_run = False

        except Exception as e:
            self.health_status.healthy = False
            self.health_status.last_error = str(e)
            await self.health_manager.update_health_file(self.current_port)

    async def watch_port(self) -> None:
        git_commit = os.getenv('GIT_COMMIT', 'unknown')
        if git_commit != 'unknown':
            short_commit = git_commit[:7]
            self.logger.info(f"Starting qSticky port manager (commit: {short_commit})...")
        else:
            self.logger.info("Starting qSticky port manager...")

        while not self.shutdown_event.is_set():
            try:
                await self.handle_port_change()
                await asyncio.sleep(self.settings.check_interval)
            except Exception as e:
                self.logger.error(f"Watch error: {str(e)}")
                self.health_status.healthy = False
                self.health_status.last_error = str(e)
                await asyncio.sleep(5)

    async def cleanup(self) -> None:
        await self.qbit.reset_session()
        try:
            if os.path.exists(self.health_manager.health_file):
                os.remove(self.health_manager.health_file)
        except Exception as e:
            self.logger.error(f"Failed to remove health file: {str(e)}")

    def setup_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self.shutdown())
            )

    async def shutdown(self) -> None:
        self.logger.info("Starting graceful shutdown...")
        self.shutdown_event.set()
        await self.qbit.reset_session()
        self.logger.info("Shutdown complete")
