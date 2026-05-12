import asyncio
import ipaddress
import json
import logging
import ssl
from typing import Any, Optional

import aiohttp
from aiohttp import ClientTimeout

from .config import HealthStatus, Settings


class QBittorrentClient:
    def __init__(self, settings: Settings, logger: logging.Logger, health_status: HealthStatus):
        self.settings = settings
        self.logger = logger
        self.health_status = health_status
        self.session: Optional[aiohttp.ClientSession] = None
        self.authenticated = False
        self.base_url = (
            f"{'https' if settings.qbittorrent_https else 'http'}"
            f"://{settings.qbittorrent_host}:{settings.qbittorrent_port}"
        )
        self._logged_in_once = False
        self.last_login_failed = False
        self._use_api_key = bool(settings.qbittorrent_api_key)
        if self._use_api_key:
            self._validate_api_key(settings.qbittorrent_api_key)
        self._use_unsafe_cookie_jar = self._is_ip_address(settings.qbittorrent_host)

    def _is_ip_address(self, host: str) -> bool:
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False

    def _validate_api_key(self, key: str) -> None:
        # qBittorrent ≥v5.2.0 requires the key to be exactly 32 chars - "qbt_" + 28 alphanumeric characters.
        valid = (
            len(key) == 32
            and key.startswith("qbt_")
            and key[4:].isalnum()
        )
        if not valid:
            self.logger.warning(
                "QBITTORRENT_API_KEY does not match the required format "
                "Generate a valid key via qBittorrent Preferences → WebUI → API Key."
            )

    def _get_cookie_jar(self) -> Optional[aiohttp.CookieJar]:
        if not self._use_unsafe_cookie_jar:
            return None
        return aiohttp.CookieJar(unsafe=True)

    async def _init_session(self) -> None:
        if self.session is not None and not self.session.closed:
            return

        self.logger.debug("Initializing new qBittorrent aiohttp session")
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
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            cookie_jar=self._get_cookie_jar()
        )
        self.authenticated = False
        self.logger.debug("qBittorrent session initialized with timeouts")

    async def reset_session(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()
            self.logger.debug("Closed qBittorrent aiohttp session")
        self.session = None
        self.authenticated = False

    async def _ensure_login(self) -> bool:
        if self._use_api_key:
            return True
        await self._init_session()
        if self.authenticated:
            return True
        return await self._login()

    async def _login(self) -> bool:
        try:
            await self._init_session()
            async with self.session.post(
                f"{self.base_url}/api/v2/auth/login",
                data={
                    "username": self.settings.qbittorrent_user,
                    "password": self.settings.qbittorrent_pass
                }
            ) as response:
                content = (await response.text()).strip()
                # qBittorrent <5.2.0  → 200 OK with body "Ok." on success
                # qBittorrent ≥5.2.0 (WebAPI 2.14.0, PR #21349) → 204 No Content on success,
                #                                                   401 Unauthorised
                if response.status == 204 or (response.status == 200 and content == "Ok."):
                    if not self._logged_in_once or self.last_login_failed:
                        self.logger.info("Successfully logged in to qBittorrent")
                        self.last_login_failed = False
                    self.authenticated = True
                    self._logged_in_once = True
                    self.health_status.healthy = True
                    self.health_status.last_error = None
                    return True

                if response.status == 401:
                    self.logger.error("Login failed: invalid credentials (HTTP 401)")
                else:
                    self.logger.error(
                        f"Login failed with status {response.status}: {content or 'empty response'}"
                    )
                self.health_status.healthy = False
                self.health_status.last_error = (
                    f"Login failed: {response.status} {content}".strip()
                )
                self.last_login_failed = True
                self.authenticated = False
                return False
        except Exception as e:
            self.logger.error(f"Login error: {str(e)}")
            self.health_status.healthy = False
            self.health_status.last_error = f"Login error: {str(e)}"
            self.last_login_failed = True
            self.authenticated = False
            return False

    async def request(
        self,
        method: str,
        path: str,
        *,
        retry: bool = True,
        **kwargs: Any
    ) -> tuple[Optional[int], Optional[str]]:
        if self._use_api_key:
            return await self._request_with_api_key(method, path, retry=retry, **kwargs)
        return await self._request_with_session(method, path, retry=retry, **kwargs)

    async def _request_with_api_key(
        self,
        method: str,
        path: str,
        *,
        retry: bool = True,
        **kwargs: Any
    ) -> tuple[Optional[int], Optional[str]]:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.settings.qbittorrent_api_key}"
        kwargs["headers"] = headers

        ssl_context = None
        if self.settings.qbittorrent_https and not self.settings.qbittorrent_verify_ssl:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        try:
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.request(
                    method,
                    f"{self.base_url}{path}",
                    **kwargs
                ) as response:
                    content = await response.text()

                    if response.status == 401:
                        self.logger.error(
                            "qBittorrent API key rejected (HTTP 401) - check QBITTORRENT_API_KEY"
                        )
                        self.health_status.healthy = False
                        self.health_status.last_error = "API key auth failed (HTTP 401)"
                        return response.status, content

                    return response.status, content
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if retry:
                self.logger.warning(
                    f"qBittorrent request to {path} failed: {str(e)}, retrying once"
                )
                return await self._request_with_api_key(method, path, retry=False, **kwargs)
            self.logger.error(f"qBittorrent request to {path} failed: {str(e)}")
            return None, None

    async def _request_with_session(
        self,
        method: str,
        path: str,
        *,
        retry: bool = True,
        **kwargs: Any
    ) -> tuple[Optional[int], Optional[str]]:
        if not await self._ensure_login():
            return None, None

        try:
            async with self.session.request(
                method,
                f"{self.base_url}{path}",
                **kwargs
            ) as response:
                content = await response.text()

                # 403 = session expired, recreate and retry.
                # 401 on ≥v5.2.0 (with updated WebAPI) = bad cred, no retry.
                if response.status == 403 and retry:
                    self.logger.warning(
                        f"qBittorrent request to {path} returned {response.status}, recreating session"
                    )
                    await self.reset_session()
                    return await self._request_with_session(method, path, retry=False, **kwargs)

                return response.status, content
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if retry:
                self.logger.warning(
                    f"qBittorrent request to {path} failed: {str(e)}, recreating session"
                )
                await self.reset_session()
                return await self._request_with_session(method, path, retry=False, **kwargs)

            self.logger.error(f"qBittorrent request to {path} failed: {str(e)}")
            return None, None

    async def get_current_port(self) -> Optional[int]:
        self.logger.debug("Retrieving current qBittorrent port")
        try:
            status, content = await self.request(
                "GET",
                "/api/v2/app/preferences",
                timeout=ClientTimeout(total=10)
            )
            if status == 200 and content is not None:
                prefs = json.loads(content)
                if prefs is None:
                    self.logger.error("Got None response from preferences API")
                    return None
                port = prefs.get('listen_port')
                self.logger.debug(f"Current qBittorrent port: {port}")
                return port

            self.logger.error(f"Failed to get preferences: {status}")
            return None
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse preferences response: {str(e)}")
            return None
        except Exception as e:
            self.logger.error(f"Error getting current port: {str(e)}")
            return None

    async def update_port(self, new_port: int) -> bool:
        if not isinstance(new_port, int) or new_port < 1024 or new_port > 65535:
            self.logger.error(f"Invalid port value: {new_port}")
            return False

        try:
            status, _ = await self.request(
                "POST",
                "/api/v2/app/setPreferences",
                data={'json': f'{{"listen_port":{new_port}}}'}
            )
            # qBittorrent ≥5.2.0 returns 204 No Content for no-body endpoints.
            if status in (200, 204):
                verified_port = await self.get_current_port()
                if verified_port == new_port:
                    return True
                self.logger.error(
                    f"Port verification failed: expected {new_port}, got {verified_port}"
                )
                return False

            self.logger.error(f"Failed to update port: {status}")
            return False
        except Exception as e:
            self.logger.error(f"Port update error: {str(e)}")
            return False
