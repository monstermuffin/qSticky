#!/usr/bin/env python3

import os
import time
import json
import logging
import aiohttp
import asyncio
import signal
import watchfiles
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
from aiohttp import ClientTimeout
from contextlib import asynccontextmanager
from pydantic import Field
from pydantic_settings import BaseSettings
from typing_extensions import Annotated

class Settings(BaseSettings):
    qbittorrent_host: Annotated[str, Field(
        description="qBittorrent server hostname"
    )] = "localhost"
    
    qbittorrent_port: Annotated[int, Field(
        description="qBittorrent server port"
    )] = 8080
    
    qbittorrent_user: Annotated[str, Field(
        description="qBittorrent username"
    )] = "admin"
    
    qbittorrent_pass: Annotated[str, Field(
        description="qBittorrent password"
    )] = "adminadmin"
    
    use_https: Annotated[bool, Field(
        description="Use HTTPS for qBittorrent connection"
    )] = False
    
    port_file_path: Annotated[str, Field(
        description="Path to Gluetun forwarded port file"
    )] = "/tmp/gluetun/forwarded_port"
    
    check_interval: Annotated[int, Field(
        description="Interval in seconds to check port file if watching fails"
    )] = 30
    
    log_level: Annotated[str, Field(
        description="Logging level"
    )] = "INFO"

    class Config:
        env_prefix = "QSTICKY_"

@dataclass
class HealthStatus:
    healthy: bool
    last_check: datetime
    last_port_change: Optional[datetime] = None
    last_error: Optional[str] = None
    current_port: Optional[int] = None
    uptime: timedelta = timedelta(seconds=0)

class PortManager:
    def __init__(self):
        self.settings = Settings()
        self.logger = self._setup_logger()
        self.current_port: Optional[int] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.base_url = f"{'https' if self.settings.use_https else 'http'}://{self.settings.qbittorrent_host}:{self.settings.qbittorrent_port}"
        self.start_time = datetime.now()
        self.health_status = HealthStatus(healthy=True, last_check=datetime.now())
        self.shutdown_event = asyncio.Event()
        self.retry_delays = [1, 2, 4, 8, 16, 32, 60]
        self.health_file = os.getenv('HEALTH_FILE', '/tmp/health_status.json')

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

    async def get_current_qbit_port(self) -> Optional[int]:
        try:
            async with self.session.get(
                f"{self.base_url}/api/v2/app/preferences",
                timeout=ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    prefs = await response.json()
                    return prefs.get('listen_port')
                else:
                    self.logger.error(f"Failed to get preferences: {response.status}")
                    return None
        except Exception as e:
            self.logger.error(f"Error getting current port: {str(e)}")
            return None

    async def _init_session(self) -> None:
        if self.session is None:
            timeout = ClientTimeout(
                total=30,
                connect=10,
                sock_connect=10,
                sock_read=10
            )
            self.session = aiohttp.ClientSession(timeout=timeout)
            self.logger.debug("Initialized new aiohttp session with timeouts")

    @asynccontextmanager
    async def _retrying_request(self):
        for delay in self.retry_delays:
            try:
                yield
                break
            except aiohttp.ClientError as e:
                self.logger.warning(f"Request failed, retrying in {delay}s: {str(e)}")
                if delay != self.retry_delays[-1]:
                    await asyncio.sleep(delay)
        else:
            self.logger.error("All retry attempts failed")
            self.health_status.healthy = False
            self.health_status.last_error = "Max retries exceeded"
            raise

    async def _login(self) -> bool:
        async with self._retrying_request():
            self.logger.debug("Attempting to login to qBittorrent")
            async with self.session.post(
                f"{self.base_url}/api/v2/auth/login",
                data={
                    "username": self.settings.qbittorrent_user,
                    "password": self.settings.qbittorrent_pass
                }
            ) as response:
                if response.status == 200:
                    self.logger.info("Successfully logged in to qBittorrent")
                    self.health_status.healthy = True
                    return True
                else:
                    self.logger.error(f"Login failed with status {response.status}")
                    self.health_status.healthy = False
                    self.health_status.last_error = f"Login failed: {response.status}"
                    return False

    async def _update_port(self, new_port: int) -> bool:
        try:
            self.logger.debug(f"Attempting to update port to {new_port}")
            async with self.session.post(
                f"{self.base_url}/api/v2/app/setPreferences",
                data={'json': f'{{"listen_port":{new_port}}}'}
            ) as response:
                if response.status == 200:
                    self.current_port = new_port
                    self.logger.info(f"Successfully updated port to {new_port}")
                    return True
                else:
                    self.logger.error(f"Failed to update port: {response.status}")
                    return False
        except Exception as e:
            self.logger.error(f"Port update error: {str(e)}")
            return False

    def _read_port_file(self) -> Optional[int]:
        try:
            if os.path.exists(self.settings.port_file_path):
                with open(self.settings.port_file_path, 'r') as f:
                    port = int(f.read().strip())
                    self.logger.debug(f"Read port {port} from file")
                    return port
            self.logger.warning(f"Port file not found at {self.settings.port_file_path}")
            return None
        except Exception as e:
            self.logger.error(f"Error reading port file: {str(e)}")
            return None

    async def handle_port_change(self) -> None:
        new_port = self._read_port_file()
        if not new_port:
            return

        await self._init_session()
        if not await self._login():
            return

        current_qbit_port = await self.get_current_qbit_port()
        
        if current_qbit_port != new_port:
            self.logger.info(f"Port change needed: {current_qbit_port} -> {new_port}")
            if await self._update_port(new_port):
                self.health_status.last_port_change = datetime.now()
                verified_port = await self.get_current_qbit_port()
                if verified_port == new_port:
                    self.logger.info("Port change verified successfully")
                else:
                    self.logger.error("Port change verification failed")
                    self.health_status.healthy = False
                    self.health_status.last_error = "Port change verification failed"
        else:
            self.logger.debug(f"Port {new_port} already set correctly")

    async def get_health(self) -> Dict[str, Any]:
        now = datetime.now()
        return {
            "healthy": self.health_status.healthy,
            "uptime": str(now - self.start_time),
            "last_check": self.health_status.last_check.isoformat(),
            "last_port_change": self.health_status.last_port_change.isoformat() if self.health_status.last_port_change else None,
            "current_port": self.current_port,
            "last_error": self.health_status.last_error,
            "timestamp": now.isoformat()
        }

    async def update_health_file(self):
        health_data = await self.get_health()
        try:
            health_dir = os.path.dirname(self.health_file)
            os.makedirs(health_dir, exist_ok=True)
            
            self.logger.debug(f"Writing health status to {self.health_file}")
            with open(self.health_file, 'w') as f:
                json.dump(health_data, f)
                self.logger.debug(f"Successfully wrote health status")
        except Exception as e:
            self.logger.error(f"Failed to write health status: {str(e)}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            
            try:
                self.logger.error(f"Current working directory: {os.getcwd()}")
                self.logger.error(f"File path absolute: {os.path.abspath(self.health_file)}")
                self.logger.error(f"Directory permissions: {oct(os.stat(health_dir).st_mode)[-3:]}")
                self.logger.error(f"Current UID: {os.getuid()}")
            except Exception as e2:
                self.logger.error(f"Failed to get additional debug info: {str(e2)}")

    async def health_check_task(self):
        while not self.shutdown_event.is_set():
            try:
                if self.session:
                    await self._login()
                self.health_status.last_check = datetime.now()
                await self.update_health_file()
                await asyncio.sleep(60)
            except Exception as e:
                self.health_status.healthy = False
                self.health_status.last_error = str(e)
                await self.update_health_file()

    async def watch_port_file(self) -> None:
        self.logger.info("Starting qSticky port manager...")
        
        await self.handle_port_change()

        try:
            async for changes in watchfiles.awatch(
                os.path.dirname(self.settings.port_file_path)
            ):
                if any(self.settings.port_file_path in change for change in changes):
                    await self.handle_port_change()
        except Exception as e:
            self.logger.error(f"Watch error: {str(e)}")
            return await self.fallback_watch()

    async def fallback_watch(self) -> None:
        self.logger.info("Falling back to interval-based checking...")
        while not self.shutdown_event.is_set():
            await self.handle_port_change()
            await asyncio.sleep(self.settings.check_interval)

    async def cleanup(self) -> None:
        if self.session:
            await self.session.close()
            self.logger.debug("Closed aiohttp session")
        try:
            if os.path.exists(self.health_file):
                os.remove(self.health_file)
        except Exception as e:
            self.logger.error(f"Failed to remove health file: {str(e)}")

    def setup_signal_handlers(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self.shutdown())
            )

    async def shutdown(self):
        self.logger.info("Starting graceful shutdown...")
        self.shutdown_event.set()
        if self.session:
            await self.session.close()
        self.logger.info("Shutdown complete")

async def main() -> None:
    manager = PortManager()
    try:
        manager.setup_signal_handlers()
        health_check_task = asyncio.create_task(manager.health_check_task())
        watch_task = asyncio.create_task(manager.watch_port_file())
        await manager.shutdown_event.wait()
        health_check_task.cancel()
        watch_task.cancel()
        try:
            await asyncio.gather(health_check_task, watch_task)
        except asyncio.CancelledError:
            pass
    except Exception as e:
        manager.logger.error(f"Unexpected error: {str(e)}")
    finally:
        await manager.cleanup()

if __name__ == "__main__":
    asyncio.run(main())