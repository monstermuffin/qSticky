import os
import json
import logging
import aiohttp
import asyncio
import signal
import ssl
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
from aiohttp import ClientTimeout
from pydantic import Field, ConfigDict
from pydantic_settings import BaseSettings
from typing_extensions import Annotated

class Settings(BaseSettings):
    # Qbit settings
    qbittorrent_host: Annotated[str, Field(
        description="qBittorrent server hostname"
    )] = "gluetun"
    
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
    
    qbittorrent_verify_ssl: Annotated[bool, Field(
        description="Verify SSL certificates for qBittorrent"
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
    )] = "gluetun"
    
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

    model_config = ConfigDict(env_prefix="")

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
        self.health_file = os.getenv('HEALTH_FILE', '/tmp/health_status.json')
        self.last_login_failed = False
        self.first_run = True
        self.last_known_port = None

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
            self.logger.debug("Initializing new aiohttp session")
            timeout = ClientTimeout(
                total=30,
                connect=10,
                sock_connect=10,
                sock_read=10
            )
            
            # https://github.com/monstermuffin/qSticky/issues/53
            ssl_context = None
            if self.settings.qbittorrent_https:
                if not self.settings.qbittorrent_verify_ssl:
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                    self.logger.debug("SSL verification disabled (default)")
                else:
                    self.logger.debug("SSL verification enabled")
            
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)
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
        except Exception as e:
            self.logger.error(f"Login error: {str(e)}")
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
                        self.health_status.last_port_change = datetime.now()
                        return True
                    else:
                        self.logger.error(f"Port verification failed: expected {new_port}, got {verified_port}")
                        return False
                else:
                    self.logger.error(f"Failed to update port: {response.status}")
                    return False
        except Exception as e:
            self.logger.error(f"Port update error: {str(e)}")
            return False

    async def _get_forwarded_port(self) -> Optional[int]:
        self.logger.debug("Attempting to get forwarded port from Gluetun")
        
        max_attempts = 3
        base_delay = 2
        
        for attempt in range(max_attempts):
            try:
                headers = {}
                auth = None

                if self.settings.gluetun_auth_type == "basic":
                    auth = aiohttp.BasicAuth(
                        self.settings.gluetun_username, 
                        self.settings.gluetun_password
                    )
                    self.logger.debug("Using basic auth")
                elif self.settings.gluetun_auth_type == "apikey":
                    headers["X-API-Key"] = self.settings.gluetun_apikey
                    self.logger.debug("Using API key auth")
                else:
                    self.logger.error("Invalid auth type specified")
                    return None

                timeout = ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    # New endpoint (Gluetun v3.39.0+)
                    async with session.get(
                        f"{self.gluetun_base_url}/v1/portforward",
                        headers=headers,
                        auth=auth
                    ) as response:
                        content = await response.text()
                        self.logger.debug(f"Gluetun API response status: {response.status}, content: {content}")
                        if response.status == 200:
                            try:
                                data = json.loads(content)
                                port = data.get("port")
                                self.logger.debug(f"Retrieved forwarded port: {port}")
                                return port
                            except json.JSONDecodeError as e:
                                self.logger.error(f"Failed to parse JSON response: {e}")
                                return None
                        elif response.status == 401:
                            # Temp fallback: Try legacy endpoint for users with old config.toml - REMOVE THIS IF YOU'RE LOOKING BACK AT THIS FOR SOME REASON
                            self.logger.warning("Got 401 on new endpoint, trying legacy endpoint /v1/openvpn/portforwarded")
                            async with session.get(
                                f"{self.gluetun_base_url}/v1/openvpn/portforwarded",
                                headers=headers,
                                auth=auth
                            ) as legacy_response:
                                if legacy_response.status == 200:
                                    try:
                                        data = json.loads(await legacy_response.text())
                                        port = data.get("port")
                                        self.logger.warning(f"Successfully retrieved port {port} from legacy endpoint.")
                                        return port
                                    except json.JSONDecodeError as e:
                                        self.logger.error(f"Failed to parse JSON response from legacy endpoint: {e}")
                                        return None
                                else:
                                    self.logger.error(f"Failed to get port from legacy endpoint: HTTP {legacy_response.status}")
                                    return None
                        else:
                            self.logger.error(f"Failed to get port: HTTP {response.status}")
                            return None
            except Exception as e:
                delay = base_delay * (attempt + 1)
                self.logger.warning(f"Connection attempt {attempt + 1} failed: {str(e)}, retrying in {delay}s...")
                await asyncio.sleep(delay)
            
        self.logger.error("All connection attempts to Gluetun failed")
        return None

    async def handle_port_change(self) -> None:
        # https://github.com/monstermuffin/qSticky/issues/53
        ssl_context = None
        if self.settings.qbittorrent_https:
            if not self.settings.qbittorrent_verify_ssl:
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
        
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(timeout=ClientTimeout(total=30), connector=connector) as session:
            self.session = session
            try:
                new_port = await self._get_forwarded_port()
                if not new_port:
                    self.health_status.healthy = False
                    return

                if not await self._login():
                    self.health_status.healthy = False
                    return

                current_qbit_port = await self.get_current_qbit_port()
                if current_qbit_port is None:
                    self.health_status.healthy = False
                    return

                self.current_port = new_port
                self.health_status.healthy = True

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
                            self.health_status.healthy = False
                            self.health_status.last_error = "Port change verification failed"
                else:
                    if self.first_run:
                        self.logger.info(f"Initial port check: {new_port} already set correctly")
                    else:
                        self.logger.debug(f"Port {new_port} already set correctly")
                    self.current_port = current_qbit_port

                await self.update_health_file()
                self.first_run = False

            except Exception as e:
                self.health_status.healthy = False
                self.health_status.last_error = str(e)
                await self.update_health_file()
            finally:
                self.session = None

    async def get_health(self) -> Dict[str, Any]:
        now = datetime.now()
        return {
            "healthy": self.health_status.healthy,
            "services": {
                "gluetun": {
                    "connected": self.health_status.healthy,
                    "port": self.current_port
                },
                "qbittorrent": {
                    "connected": self.health_status.healthy and self.current_port is not None,
                    "port_synced": self.current_port is not None
                }
            },
            "uptime": str(now - self.start_time),
            "last_check": self.health_status.last_check.isoformat(),
            "last_port_change": self.health_status.last_port_change.isoformat() 
                if self.health_status.last_port_change else None,
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
                # Try new endpoint first (Gluetun v3.39.0+)
                async with session.get(
                    f"{self.gluetun_base_url}/v1/vpn/status",
                    headers=headers,
                    auth=auth
                ) as response:
                    self.logger.debug(f"Connectivity check status: {response.status}")
                    if response.status == 200:
                        return True
                    elif response.status == 401:
                        # TEMPORARY FALLBACK: Try legacy endpoint for users with old config.toml
                        # TODO: Remove this fallback after v3.0.0 (added 2024-11-18)
                        self.logger.debug("Got 401 on new status endpoint, trying legacy endpoint /v1/openvpn/status")
                        async with session.get(
                            f"{self.gluetun_base_url}/v1/openvpn/status",
                            headers=headers,
                            auth=auth
                        ) as legacy_response:
                            return legacy_response.status == 200
                    return False
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