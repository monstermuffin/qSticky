# qSticky
qSticky is an automated port forwarding manager for Gluetun and qBittorrent. It automatically updates qBittorrent's listening port whenever Gluetun receives a new forwarded port.

![](img/scrn_rec.gif)

![](img/SCR-20241218-lfff.png)

## Features
- 🔄 Automatic port synchronization
- 👀 Real-time file watching with fallback polling
- 🔒 Secure HTTPS support
- 🐳 Docker deployment
- 📝 Logging

## How it Works

qSticky checks Gluetun's VPN port forwarding file and updates this as required in qBittorrent's connection settings.

1. **Port Monitoring**
   - Gluetun writes the forwarded port number to `/tmp/gluetun/forwarded_port`
   - qSticky actively monitors this file for changes using file system events
   - If event watching fails, falls back to polling every 30 seconds (configurable)

2. **Port Management**
   - When a new port is detected:
     - Reads the new port number from the file
     - Connects to qBittorrent's WebUI API
     - Updates qBittorrent's listening port
     - Verifies the change was successful

3. **Health Monitoring**
   - Maintains a health status file
   - Checks qBittorrent connectivity regularly
   - Tracks port changes and any errors
   - Provides Docker health checks

4. **Recovery & Resilience**
   - Automatically retries on connection failures
   - Maintains session with qBittorrent
   - Handles network interruptions gracefully
   - Logs important events and errors

### Flow
```mermaid
graph TD;
    Gluetun-->|Writes Port|File;
    File-->|Monitors|qSticky;
    qSticky-->|Updates Port|qBit;
    qSticky-->|Writes|Health;
```

## Quick Start

> [!IMPORTANT]  
> Your VPN provider must support port forwarding! Some allocate static ports so this isn't required, however, others give you a port at random and which is where qSticky comes

### Port Forwarding Setup

qSticky needs access to Gluetun's forwarded port file to function. When Gluetun successfully sets up port forwarding, it writes the port number to `/tmp/gluetun/forwarded_port`. This file is monitored by qSticky to detect port changes.

To give qSticky access to this file, you need to:
1. Enable port forwarding in Gluetun by setting `VPN_PORT_FORWARDING=on`
2. Expose `/tmp/gluetun/forwarded_port` to the host OS
3. Mount `/tmp/gluetun/forwarded_port` exposed to the host into the qSticky container
4. Ensure both containers share network access (using `network_mode: "service:gluetun"`)

A working Gluetun configuration **might** look like:
```yaml
services:
  gluetun:
    image: qmcgaw/gluetun:latest
    volumes:
      - /your/tmp/location:/tmp/gluetun  # This folder must be mounted in qSticky
    environment:
      - VPN_SERVICE_PROVIDER=protonvpn
      - VPN_TYPE=wireguard
      - VPN_PORT_FORWARDING=on
```

### Using Docker Compose

> [!TIP]
> A full list of environment variables are listed and explained below.

To deploy qSticky, add the service to your compose file as so, changing settings as required:
```yaml
services:
  qsticky:
    image: ghcr.io/monstermuffin/qsticky:latest
    restart: unless-stopped
    volumes:
      - /your/tmp/location:/tmp/gluetun
    network_mode: "service:gluetun"
    environment:
      - QSTICKY_QBITTORRENT_HOST=localhost
      - QSTICKY_QBITTORRENT_PORT=8080
      - QSTICKY_QBITTORRENT_USER=admin
      - QSTICKY_QBITTORRENT_PASS=adminadmin
      - QSTICKY_USE_HTTPS=false
      - QSTICKY_PORT_FILE_PATH=/tmp/gluetun/forwarded_port
      - QSTICKY_CHECK_INTERVAL=30
      - QSTICKY_LOG_LEVEL=INFO
```

Here is an example stack for deploying Gluetun, qBitorrent and qSticky:

> [!NOTE]  
> Your VPN configuration will look somewhat different to very different based on protocol and/or provider. Please check the gluetun docs. You should get gluetun working before setting up qSticky if unsure.

```yaml
services:
  gluetun:                                        # gluetun Container
    image: qmcgaw/gluetun:latest
    container_name: gluetun
    cap_add:
      - NET_ADMIN                                 # Required for VPN functionality
    volumes:
      - ./gluetun:/tmp/gluetun                    # Port forwarding file location
    environment:           # VPN Configuration - You may be doing this as below or via a file.
      - VPN_SERVICE_PROVIDER=protonvpn            # Change to your VPN provider
      - VPN_TYPE=wireguard
      - WIREGUARD_PRIVATE_KEY=your_private_key    # Add your WireGuard key
      - WIREGUARD_ADDRESSES=your_wireguard_ip     # Add your WireGuard IP
      - SERVER_COUNTRIES=Netherlands              # Choose your preferred country
      - VPN_PORT_FORWARDING=on                    # Port Forwarding (required for qSticky)
    ports:
      - 8080:8080                                 # qBittorrent WebUI
    restart: unless-stopped

  qbittorrent:                                    # qBittorrent Container
    image: linuxserver/qbittorrent:latest
    container_name: qbittorrent
    network_mode: "service:gluetun"               # Use Gluetun's network
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=UTC
      - WEBUI_PORT=8080
    volumes:
      - ./qbittorrent/config:/config
      - ./downloads:/downloads
    restart: unless-stopped
    depends_on:
      - gluetun

  qsticky:                                        # qSticky Container
    image: ghcr.io/monstermuffin/qsticky:latest
    container_name: qsticky
    network_mode: "service:gluetun"               # Use Gluetun's network
    volumes:
    - ./gluetun:/tmp/gluetun                      # Mount the same Gluetun volume for port mapping
    environment:
      - QSTICKY_QBITTORRENT_HOST=localhost
      - QSTICKY_QBITTORRENT_PORT=8080
      - QSTICKY_QBITTORRENT_USER=admin
      - QSTICKY_QBITTORRENT_PASS=adminadmin
      - QSTICKY_USE_HTTPS=false
      - QSTICKY_PORT_FILE_PATH=/tmp/gluetun/forwarded_port
      - QSTICKY_CHECK_INTERVAL=30
      - QSTICKY_LOG_LEVEL=INFO
    restart: unless-stopped
    depends_on:
      - gluetun
      - qbittorrent
```

### Configuration

All configuration is done through environment variables:

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| QSTICKY_QBITTORRENT_HOST | qBittorrent server hostname | localhost |
| QSTICKY_QBITTORRENT_PORT | qBittorrent server port | 8080 |
| QSTICKY_QBITTORRENT_USER | qBittorrent username | admin |
| QSTICKY_QBITTORRENT_PASS | qBittorrent password | adminadmin |
| QSTICKY_USE_HTTPS | Use HTTPS for qBittorrent connection | false |
| QSTICKY_PORT_FILE_PATH | Path to Gluetun forwarded port file | /tmp/gluetun/forwarded_port |
| QSTICKY_CHECK_INTERVAL | Fallback check interval in seconds | 30 |
| QSTICKY_LOG_LEVEL | Logging level (DEBUG, INFO, WARNING, ERROR) | INFO |
| HEALTH_FILE | Path to health status file | /tmp/health/status.json |

### Network Configuration

When using `network_mode: "service:gluetun"`, all containers share Gluetun's network stack. This is most likely how you have your stack configured to tunnel qBitorrent via Gluetun, and so qSticky can be deployed as so:

```yaml
services:
  gluetun:
    # ... gluetun config ...

  qbittorrent:
    network_mode: "service:gluetun"
    environment:
      - WEBUI_PORT=8080
  
  qsticky:
    network_mode: "service:gluetun"
    environment:
      - QSTICKY_QBITTORRENT_HOST=localhost  # Using localhost works here
      - QSTICKY_QBITTORRENT_PORT=8080
```

### Checks
To check qSticky is working:

  - Check qSticky logs with `docker logs qSticky`, or whatever you named the container.
  - Check qBitorrent settings have updated. qSticky does verify this and will output in the log, but you can check yourself for piece of mind. 
  - If qSticky isn't finding a port, ensure `/tmp/gluetun/forwarded_port` is properly mounted on the host and contains a port number.
  - If `/tmp/gluetun/forwarded_port` exists on the host and contains a port number but qSticky isn't working still, enter the containers shell and ensure the file exists inside the container at `/tmp/gluetun`.

When successful, the logs will look something like the following:

```bash
2025-01-13 20:08:13,178 - qsticky - INFO - Starting qSticky port manager...
2025-01-13 20:08:13,185 - qsticky - INFO - Successfully logged in to qBittorrent
2025-01-13 20:08:13,186 - qsticky - INFO - Port change needed: 58988 -> 51218
2025-01-13 20:08:13,189 - qsticky - INFO - Successfully updated port to 51218
```

### User Permissions
qSticky can run as any user, which is particularly useful when running with qBittorrent's user permissions. To run as a specific user, use the `user:` directive in your docker-compose file:

```yaml
services:
  qsticky:
    image: ghcr.io/monstermuffin/qsticky:latest
    user: "your-qbittorrent-user"  # Optional: Run as specific user
```

### Health Checks
qSticky includes Docker health checks. The health status is written to a file at `/app/health/status.json`. This file is managed internally by the container - you don't need to mount or manage it. Health status includes:
  - Overall health status
  - Uptime
  - Last check timestamp
  - Last port change time
  - Current port
  - Last error (if any)

The Docker container will be marked as unhealthy if:
  - The application fails to write health status
  - qBittorrent becomes unreachable
  - Port updates fail repeatedly
  - Other critical errors occur

## Development

### Prerequisites
- Python 3.11+
- pip

### Setup

1. Clone the repository:
```bash
git clone https://github.com/monstermuffin/qsticky.git
cd qsticky
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the application:
```bash
python qsticky.py
```