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
    # Qbit settings
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
    
    qbittorrent_https: Annotated[bool, Field(
        description="Use HTTPS for qBittorrent connection"
    )] = False
    
    check_interval: Annotated[int, Field(
        description="Interval in seconds between port checks"
    )] = 30
    
    log_level: Annotated[str, Field(
        description="Logging level"
    )] = "INFO"

    # Gluetun control server settings
    gluetun_host: Annotated[str, Field(
        description="Gluetun control server hostname"
    )] = "localhost"
    
    gluetun_port: Annotated[int, Field(
        description="Gluetun control server port"
    )] = 8000
    
    gluetun_auth_type: Annotated[str, Field(
        description="Gluetun authentication type (basic/apikey)"
    )] = "apikey"
    
    gluetun_username: Annotated[str, Field(
        description="Gluetun basic auth username"
    )] = ""
    
    gluetun_password: Annotated[str, Field(
        description="Gluetun basic auth password"
    )] = ""
    
    gluetun_apikey: Annotated[str, Field(
        description="Gluetun API key"
    )] = ""

    class Config:
        env_prefix = ""

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
        self.base_url = f"{'https' if self.settings.qbittorrent_https else 'http'}://{self.settings.qbittorrent_host}:{self.settings.qbittorrent_port}"
        self.gluetun_base_url = f"http://{self.settings.gluetun_host}:{self.settings.gluetun_port}"
        self.start_time = datetime.now()
        self.health_status = HealthStatus(healthy=True, last_check=datetime.now())
        self.shutdown_event = asyncio.Event()
        self.retry_delays = [1, 2, 4, 8, 16, 32, 60]
        self.health_file = os.getenv('HEALTH_FILE', '/tmp/health_status.json')
        self.last_login_failed = False
        self.first_run = True

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
                    if self.first_run or self.last_login_failed:
                        self.logger.info("Successfully logged in to qBittorrent")
                        self.last_login_failed = False
                    self.health_status.healthy = True
                    return True
                else:
                    self.logger.error(f"Login failed with status {response.status}")
                    self.health_status.healthy = False
                    self.health_status.last_error = f"Login failed: {response.status}"
                    self.last_login_failed = True
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
                    return True
                else:
                    self.logger.error(f"Failed to update port: {response.status}")
                    return False
        except Exception as e:
            self.logger.error(f"Port update error: {str(e)}")
            return False

    async def _get_forwarded_port(self) -> Optional[int]:
        await self._init_session()
        try:
            headers = {}
            auth = None

            if self.settings.gluetun_auth_type == "basic":
                auth = aiohttp.BasicAuth(
                    self.settings.gluetun_username, 
                    self.settings.gluetun_password
                )
            elif self.settings.gluetun_auth_type == "apikey":
                headers["X-API-Key"] = self.settings.gluetun_apikey
                self.logger.debug(f"Using API key auth with headers: {headers}")
            else:
                self.logger.error("Invalid auth type specified")
                return None

            self.logger.debug(f"Using auth type: {self.settings.gluetun_auth_type}")

            async with self.session.get(
                f"{self.gluetun_base_url}/v1/openvpn/portforwarded",
                headers=headers,
                auth=auth,
                timeout=ClientTimeout(total=10)
            ) as response:
                content = await response.text()
                self.logger.debug(f"Response status: {response.status}, content: {content}, content-type: {response.headers.get('content-type')}")
                if response.status == 200:
                    try:
                        data = json.loads(content)
                        port = data.get("port")
                        self.logger.debug(f"Retrieved forwarded port: {port}")
                        return port
                    except json.JSONDecodeError as e:
                        self.logger.error(f"Failed to parse JSON response: {e}")
                        return None
                else:
                    self.logger.error(f"Failed to get port: HTTP {response.status}")
                    return None
        except Exception as e:
            self.logger.error(f"Error getting forwarded port: {str(e)}")
            return None

    async def handle_port_change(self) -> None:
        new_port = await self._get_forwarded_port()
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
                    self.logger.info(f"Successfully updated port to {new_port}")
                else:
                    self.logger.error("Port change verification failed")
                    self.health_status.healthy = False
                    self.health_status.last_error = "Port change verification failed"
        else:
            if self.first_run:
                self.logger.info(f"Initial port check: {new_port} already set correctly")
            else:
                self.logger.debug(f"Port {new_port} already set correctly")
        
        self.first_run = False

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

    async def watch_port(self) -> None:
        self.logger.info("Starting qSticky port manager...")
        
        await self.handle_port_change()

        while not self.shutdown_event.is_set():
            try:
                await self.handle_port_change()
                await asyncio.sleep(self.settings.check_interval)
            except Exception as e:
                self.logger.error(f"Watch error: {str(e)}")
                self.health_status.healthy = False
                self.health_status.last_error = str(e)
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
        watch_task = asyncio.create_task(manager.watch_port())
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