import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from .config import HealthStatus


class HealthManager:
    def __init__(self, health_status: HealthStatus, health_file: str, logger: logging.Logger):
        self.health_status = health_status
        self.health_file = health_file
        self.logger = logger
        self.start_time = datetime.now()

    def get_health(self, current_port: Optional[int]) -> Dict[str, Any]:
        now = datetime.now()
        return {
            "healthy": self.health_status.healthy,
            "services": {
                "gluetun": {
                    "connected": self.health_status.healthy,
                    "port": current_port
                },
                "qbittorrent": {
                    "connected": self.health_status.healthy and current_port is not None,
                    "port_synced": current_port is not None
                }
            },
            "uptime": str(now - self.start_time),
            "last_check": self.health_status.last_check.isoformat(),
            "last_port_change": (
                self.health_status.last_port_change.isoformat()
                if self.health_status.last_port_change else None
            ),
            "timestamp": now.isoformat()
        }

    async def update_health_file(self, current_port: Optional[int]) -> None:
        health_data = self.get_health(current_port)
        try:
            health_dir = os.path.dirname(self.health_file)
            os.makedirs(health_dir, exist_ok=True)
            self.logger.debug(f"Writing health status to {self.health_file}")
            with open(self.health_file, 'w') as f:
                json.dump(health_data, f)
            self.logger.debug("Successfully wrote health status")
        except Exception as e:
            self.logger.error(f"Failed to write health status: {str(e)}")
