from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
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

    qbittorrent_api_key: Annotated[str, Field(
        description=(
            "qBittorrent API key (v5+)."
            "Generate via qBittorrent Preferences → WebUI → API Key."
        )
    )] = ""

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
