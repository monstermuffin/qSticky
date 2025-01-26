import os
import json
import logging
import aiohttp
import asyncio
import signal
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from aiohttp import ClientTimeout
from pydantic import Field
from pydantic_settings import BaseSettings
from typing_extensions import Annotated

class HealthFileHandler:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.file = None
        
    async def __aenter__(self):
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        self.file = open(self.file_path, 'w')
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.file:
            self.file.close()

    def write_health(self, health_data: dict):
        json.dump(health_data, self.file)
        self.file.flush()

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
   gluetun_healthy: bool = True
   qbit_healthy: bool = True
   last_check: datetime = field(default_factory=datetime.now)
   last_port_change: Optional[datetime] = None
   last_error: Optional[str] = None
   current_port: Optional[int] = None
   uptime: timedelta = timedelta(seconds=0)

class PortManager:
    def __init__(self):
        self.health_handler = None
        
        self.default_timeout = ClientTimeout(
            total=30,
            connect=10,
            sock_connect=10,
            sock_read=10
        )
        self.settings = Settings()
        self.logger = self._setup_logger()
        self.current_port: Optional[int] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.base_url = f"{'https' if self.settings.qbittorrent_https else 'http'}://{self.settings.qbittorrent_host}:{self.settings.qbittorrent_port}"
        self.gluetun_base_url = f"http://{self.settings.gluetun_host}:{self.settings.gluetun_port}"
        self.start_time = datetime.now()
        self.health_status = HealthStatus()
        self.shutdown_event = asyncio.Event()
        self.health_file = os.getenv('HEALTH_FILE', '/tmp/health_status.json')
        self.last_login_failed = False
        self.last_known_port = None

    async def start(self):
        self.health_handler = HealthFileHandler(self.health_file)
        await self.health_handler.__aenter__()

    async def cleanup(self) -> None:
        if self.session:
            await self.session.close()
            self.logger.debug("Closed aiohttp session")
        if self.health_handler:
            await self.health_handler.__aexit__(None, None, None)
        try:
            if os.path.exists(self.health_file):
                os.remove(self.health_file)
        except Exception as e:
            self.logger.error(f"Failed to remove health file: {str(e)}")

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
        self.logger.debug("Retrieving current qBittorrent port")
        try:
            async with self.session.get(f"{self.base_url}/api/v2/app/preferences", timeout=ClientTimeout(total=10)) as response:
                if response.status == 200:
                    prefs = await response.json()
                    if prefs is None:
                        self.logger.error("Got None response from preferences API")
                        return None
                    port = prefs.get('listen_port')
                    self.logger.debug(f"Current qBittorrent port: {port}")
                    return port
                else:
                    self.logger.error(f"Failed to get preferences: {response.status}")
                    return None
        except Exception as e:
            self.logger.error(f"Error getting current port: {str(e)}")
            return None

    async def _init_session(self) -> None:
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.default_timeout)
            self.logger.debug("Session initialized with timeouts")

    async def _login(self) -> bool:
        try:
            async with self.session.post(
                f"{self.base_url}/api/v2/auth/login",
                data={
                    "username": self.settings.qbittorrent_user,
                    "password": self.settings.qbittorrent_pass
                }
            ) as response:
                if response.status == 200:
                    self.health_status.qbit_healthy = True
                    if self.last_login_failed:
                        self.logger.info("Successfully logged in to qBittorrent")
                        self.last_login_failed = False
                    return True
                else:
                    self.logger.error(f"Login failed with status {response.status}")
                    self.health_status.last_error = f"Login failed: {response.status}"
                    self.last_login_failed = True
                    return False
        except Exception as e:
            self.logger.error(f"Login error: {str(e)}")
            self.health_status.qbit_healthy = False
            await self.update_health_file()
            return False

    async def _update_port(self, new_port: int) -> bool:
        if not isinstance(new_port, int) or new_port < 1024 or new_port > 65535:
            self.logger.error(f"Invalid port value: {new_port}")
            return False
            
        try:
            async with self.session.post(
                f"{self.base_url}/api/v2/app/setPreferences",
                data={'json': f'{{"listen_port":{new_port}}}'}
            ) as response:
                if response.status == 200:
                    verified_port = await self.get_current_qbit_port()
                    if verified_port == new_port:
                        self.current_port = new_port
                        self.health_status.qbit_healthy = True
                        self.health_status.last_port_change = datetime.now()
                        return True
                    else:
                        self.logger.error(f"Port verification failed: expected {new_port}, got {verified_port}")
                        return False
                else:
                    self.logger.error(f"Failed to update port: {response.status}")
                    return False
        except Exception as e:
            self.logger.error(f"Error getting current port: {str(e)}")
            await self.update_health_file()
            return None

    async def _get_forwarded_port(self) -> Optional[int]:
        self.logger.debug("Attempting to get forwarded port from Gluetun")
        
        max_attempts = 3
        base_delay = 2
        
        headers = {}
        auth = None

        if self.settings.gluetun_auth_type == "basic":
            auth = aiohttp.BasicAuth(
                self.settings.gluetun_username, 
                self.settings.gluetun_password
            )
        elif self.settings.gluetun_auth_type == "apikey":
            headers["X-API-Key"] = self.settings.gluetun_apikey
        else:
            self.logger.error("Invalid auth type specified")
            return None

        for attempt in range(max_attempts):
            try:
                async with aiohttp.ClientSession(timeout=self.default_timeout) as session:
                    async with session.get(
                        f"{self.gluetun_base_url}/v1/openvpn/portforwarded",
                        headers=headers,
                        auth=auth
                    ) as response:
                        if response.status == 401:
                            self.logger.error("Authentication failed")
                            return None
                            
                        content = await response.text()
                        if response.status == 200:
                            try:
                                data = json.loads(content)
                                port = data.get("port")
                                if port:
                                    self.health_status.gluetun_healthy = True
                                    self.health_status.current_port = port
                                    return port
                                self.logger.error("No port in response")
                                return None
                            except json.JSONDecodeError as e:
                                self.logger.error(f"Failed to parse JSON response: {e}")
                                return None
                        else:
                            self.logger.error(f"Failed to get port: HTTP {response.status}")
                            return None
                            
            except asyncio.TimeoutError:
                delay = base_delay * (attempt + 1)
                self.logger.warning(f"Timeout on attempt {attempt + 1}, retrying in {delay}s...")
                await asyncio.sleep(delay)
            except Exception as e:
                self.logger.error(f"Unexpected error: {str(e)}")
                return None

        self.logger.error("All connection attempts to Gluetun failed")
        self.health_status.gluetun_healthy = False
        return None

    async def handle_port_change(self) -> None:
        try:
            await self._init_session()
            
            new_port = await self._get_forwarded_port()
            if not new_port:
                self.health_status.gluetun_healthy = False
                return

            self.current_port = new_port
            self.health_status.current_port = new_port
            self.health_status.gluetun_healthy = True

            if not await self._login():
                self.health_status.qbit_healthy = False
                return
                
            current_qbit_port = await self.get_current_qbit_port()
            if current_qbit_port is None:
                self.health_status.qbit_healthy = False
                return

            self.health_status.qbit_healthy = True

            if current_qbit_port != new_port:
                self.logger.info(f"Port change needed: {current_qbit_port} -> {new_port}")
                if await self._update_port(new_port):
                    self.health_status.last_port_change = datetime.now()
                    verified_port = await self.get_current_qbit_port()
                    if verified_port == new_port:
                        self.logger.info(f"Successfully updated port to {new_port}")
                        self.current_port = new_port
                    else:
                        self.logger.error(f"Port change verification failed - expected {new_port}, got {verified_port}")
                        self.health_status.qbit_healthy = False
                        self.health_status.last_error = "Port change verification failed"
            else:
                self.logger.debug(f"Port {new_port} already set correctly")
                self.current_port = current_qbit_port

            await self.update_health_file()

        except Exception as e:
            self.health_status.qbit_healthy = False 
            self.health_status.last_error = str(e)
            await self.update_health_file()
        finally:
            if self.session:
                await self.session.close()
                self.session = None

    async def get_health(self) -> Dict[str, Any]:
        now = datetime.now()
        return {
            "healthy": self.health_status.gluetun_healthy and self.health_status.qbit_healthy,
            "services": {
                "gluetun": {
                    "connected": self.health_status.gluetun_healthy,
                    "port": self.current_port
                },
                "qbittorrent": {
                    "connected": self.health_status.qbit_healthy,
                    "port_synced": self.current_port is not None and self.health_status.qbit_healthy
                }
            },
            "uptime": str(now - self.start_time),
            "last_check": self.health_status.last_check.isoformat(),
            "last_port_change": self.health_status.last_port_change.isoformat() if self.health_status.last_port_change else None,
            "timestamp": now.isoformat()
        }

    async def check_connectivity(self) -> bool:
        headers = {}
        auth = None

        if self.settings.gluetun_auth_type == "basic":
            auth = aiohttp.BasicAuth(self.settings.gluetun_username, self.settings.gluetun_password)
        elif self.settings.gluetun_auth_type == "apikey":
            headers["X-API-Key"] = self.settings.gluetun_apikey

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.gluetun_base_url}/v1/openvpn/status",
                    headers=headers,
                    auth=auth
                ) as response:
                    self.logger.debug(f"Connectivity check status: {response.status}")
                    return response.status == 200
        except Exception as e:
            self.logger.debug(f"Connectivity check failed: {str(e)}")
            return False

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

    async def watch_port(self) -> None:
        self.logger.info("Starting qSticky port manager...")
        
        while not self.shutdown_event.is_set():
            try:
                watch_task = asyncio.create_task(self.handle_port_change())
                await asyncio.wait_for(watch_task, timeout=60)  # 1 minute timeout
                await asyncio.sleep(self.settings.check_interval)
            except asyncio.TimeoutError:
                self.logger.error("Port check timed out")
                self.health_status.last_error = "Port check timeout"
                await self.update_health_file()
            except Exception as e:
                self.logger.error(f"Watch error: {str(e)}")
                self.health_status.last_error = str(e)
                await asyncio.sleep(5)

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
        await manager.start()
        tasks = [
            asyncio.create_task(manager.watch_port())
        ]
        await manager.shutdown_event.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await manager.cleanup()

if __name__ == "__main__":
    asyncio.run(main())