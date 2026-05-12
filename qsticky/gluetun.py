import asyncio
import json
import logging
from typing import Optional

import aiohttp
from aiohttp import ClientTimeout

from .config import Settings


class GluetunClient:
    def __init__(self, settings: Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger
        self.base_url = f"http://{settings.gluetun_host}:{settings.gluetun_port}"

    def _get_auth(self) -> tuple[Optional[aiohttp.BasicAuth], dict]:
        if self.settings.gluetun_auth_type == "basic":
            return aiohttp.BasicAuth(
                self.settings.gluetun_username,
                self.settings.gluetun_password
            ), {}
        if self.settings.gluetun_auth_type == "apikey":
            return None, {"X-API-Key": self.settings.gluetun_apikey}
        return None, {}

    async def get_forwarded_port(self) -> Optional[int]:
        self.logger.debug("Attempting to get forwarded port from Gluetun")

        if self.settings.gluetun_auth_type not in ("basic", "apikey"):
            self.logger.error("Invalid auth type specified")
            return None

        max_attempts = 3
        base_delay = 2

        for attempt in range(max_attempts):
            try:
                auth, headers = self._get_auth()
                timeout = ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    # New endpoint (Gluetun v3.39.0+)
                    async with session.get(
                        f"{self.base_url}/v1/portforward",
                        headers=headers,
                        auth=auth
                    ) as response:
                        content = await response.text()
                        self.logger.debug(
                            f"Gluetun API response status: {response.status}, content: {content}"
                        )
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
                            self.logger.warning(
                                "Got 401 on new endpoint, trying legacy endpoint /v1/openvpn/portforwarded"
                            )
                            async with session.get(
                                f"{self.base_url}/v1/openvpn/portforwarded",
                                headers=headers,
                                auth=auth,
                                allow_redirects=False
                            ) as legacy_response:
                                if legacy_response.status == 200:
                                    try:
                                        data = json.loads(await legacy_response.text())
                                        port = data.get("port")
                                        self.logger.warning(
                                            f"Successfully retrieved port {port} from legacy endpoint. "
                                            "Please update your config.toml to include 'GET /v1/portforward'"
                                        )
                                        return port
                                    except json.JSONDecodeError as e:
                                        self.logger.error(
                                            f"Failed to parse JSON response from legacy endpoint: {e}"
                                        )
                                        return None
                                elif legacy_response.status == 301:
                                    self.logger.error(
                                        "Legacy endpoint redirects to new endpoint, but new endpoint not "
                                        "authorised. Please update your config.toml: "
                                        "https://github.com/monstermuffin/qSticky/tree/main?tab=readme-ov-file#authentication-setup"
                                    )
                                    return None
                                else:
                                    self.logger.error(
                                        f"Failed to get port from legacy endpoint: HTTP {legacy_response.status}"
                                    )
                                    return None
                        else:
                            self.logger.error(f"Failed to get port: HTTP {response.status}")
                            return None
            except Exception as e:
                delay = base_delay * (attempt + 1)
                self.logger.warning(
                    f"Connection attempt {attempt + 1} failed: {str(e)}, retrying in {delay}s..."
                )
                await asyncio.sleep(delay)

        self.logger.error("All connection attempts to Gluetun failed")
        return None

    async def check_connectivity(self) -> bool:
        auth, headers = self._get_auth()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/v1/vpn/status",
                    headers=headers,
                    auth=auth
                ) as response:
                    self.logger.debug(f"Connectivity check status: {response.status}")
                    if response.status == 200:
                        return True
                    elif response.status == 401:
                        # TEMPORARY FALLBACK: Try legacy endpoint for users with old config.toml
                        # TODO: Remove this fallback after v3.0.0 (added 2024-11-18)
                        self.logger.debug(
                            "Got 401 on new status endpoint, trying legacy endpoint /v1/openvpn/status"
                        )
                        async with session.get(
                            f"{self.base_url}/v1/openvpn/status",
                            headers=headers,
                            auth=auth,
                            allow_redirects=False
                        ) as legacy_response:
                            if legacy_response.status == 301:
                                self.logger.debug("Legacy status endpoint redirects to new endpoint")
                                return False
                            return legacy_response.status == 200
                    return False
        except Exception as e:
            self.logger.debug(f"Connectivity check failed: {str(e)}")
            return False
