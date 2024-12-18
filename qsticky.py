#!/usr/bin/env python3

import os
import time
import logging
import aiohttp
import asyncio
import watchfiles
from typing import Optional
from pydantic import BaseSettings, Field
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

class PortManager:
    def __init__(self):
        self.settings = Settings()
        self.logger = self._setup_logger()
        self.current_port: Optional[int] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.base_url = f"{'https' if self.settings.use_https else 'http'}://{self.settings.qbittorrent_host}:{self.settings.qbittorrent_port}"

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

    async def _init_session(self) -> None:
        if self.session is None:
            self.session = aiohttp.ClientSession()
            self.logger.debug("Initialized new aiohttp session")

    async def _login(self) -> bool:
        try:
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
                    return True
                else:
                    self.logger.error(f"Login failed with status {response.status}")
                    return False
        except Exception as e:
            self.logger.error(f"Login error: {str(e)}")
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
        if new_port and new_port != self.current_port:
            self.logger.info(f"Port change detected: {self.current_port} -> {new_port}")
            await self._init_session()
            if await self._login():
                await self._update_port(new_port)

    async def watch_port_file(self) -> None:
        self.logger.info("Starting qSticky port manager...")
        
        # Initial port check
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
        while True:
            await self.handle_port_change()
            await asyncio.sleep(self.settings.check_interval)

    async def cleanup(self) -> None:
        if self.session:
            await self.session.close()
            self.logger.debug("Closed aiohttp session")

async def main() -> None:
    manager = PortManager()
    try:
        await manager.watch_port_file()
    except KeyboardInterrupt:
        manager.logger.info("Received shutdown signal")
    except Exception as e:
        manager.logger.error(f"Unexpected error: {str(e)}")
    finally:
        await manager.cleanup()

if __name__ == "__main__":
    asyncio.run(main())