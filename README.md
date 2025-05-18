# qSticky
qSticky is an automated port forwarding manager for Gluetun and qBittorrent. It automatically updates qBittorrent's listening port whenever Gluetun receives a new forwarded port.

![](img/SCR-20241218-lfff.png)

> [!CAUTION]
> qSticky v2.0 was refactored to work with Gluetun's control server API instead of the forwarded file as this is being deprecated.

## 🛠️ How it Works
qSticky monitors Gluetun's port forwarding through its [control server API](https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/control-server.md#openvpn-and-wireguard) and updates qBittorrent's connection settings as needed.

1. **Port Monitoring**
   - qSticky queries Gluetun's control server API endpoint at `/v1/openvpn/portforwarded`
   - Supports both Basic Auth and API Key authentication methods
   - Polls the API at configurable intervals (default: 30 seconds)

2. **Port Management**
   - When a new port is detected:
     - Retrieves the port number from Gluetun's API
     - Connects to qBittorrent's WebUI API
     - Updates qBittorrent's listening port
     - Verifies the change was successful

3. **Health Monitoring**
   - Maintains a health status file
   - Checks qBittorrent connectivity regularly
   - Tracks port changes and any errors
   - Provides Docker health checks

4. **Recovery**
   - Automatically retries on connection failures
   - Maintains session with qBittorrent
   - Handles network interruptions gracefully
   - Logs important events and errors

### Flow
```mermaid
graph TD;
    A[Gluetun Control Server] -->|API Poll| B[qSticky];
    B -->|Port Update| C[qBittorrent];
    B -->|Status Write| D[Health Monitor];
```

## 🚀 Quick Start

> [!IMPORTANT]  
> qSticky only supports [whatever gluetun natively supports for automatic port forwarding.](https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/vpn-port-forwarding.md#native-integrations) At time of writing, this is PIA and ProtonVPN.

## 🔑 Authentication Setup
qSticky requires access to Gluetun's control server API to monitor port forwarding. You need to configure this one of two ways:

> [!IMPORTANT]  
> Authentication is required for qSticky to function. No unauthenticated access is supported, as Gluetun is deprecating unauthenticated endpoints.

### Create Authentication Config
Create a `config.toml` file somewhere to be mapped into gluetun:
```toml
[[roles]]
name = "qSticky"
routes = [
    "GET /v1/openvpn/portforwarded",
    "GET /v1/openvpn/status"
]
auth = "apikey"
apikey = "your_api_key_here"
```

Or if you prefer basic auth:
```toml
[[roles]]
name = "qSticky"
routes = [
    "GET /v1/openvpn/portforwarded",
    "GET /v1/openvpn/status"
]
auth = "basic"
username = "myusername"
password = "mypassword"
```
> [!NOTE]
>`/v1/openvpn/portforwarded` is required for the dynamic port mapping, and `/v1/openvpn/status` is required for gluetun's health status.


### Volume mount
Mount the config in your docker-compose.yml:
```yaml
services:
  gluetun:
    # ... other gluetun config ...
    volumes:
      - ./gluetun/config.toml:/gluetun/auth/config.toml  # Mount auth config
```

### qSticky Configuration
Configure qSticky to use the same authentication method:
```yaml
services:
  qSticky:
    # ... other qSticky config ...
    environment:
      # For API Key auth:
      GLUETUN_AUTH_TYPE: apikey
      GLUETUN_APIKEY: your_api_key_here

      # Or for Basic auth:
      # GLUETUN_AUTH_TYPE: basic
      # GLUETUN_USERNAME: myusername
      # GLUETUN_PASSWORD: mypassword
```

For complete details on Gluetun's control server authentication, check out the [official Gluetun documentation.](https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/control-server.md#openvpn-and-wireguard)

## 🐋 Gluetun Setup
Gluetun setup is simple and if you're already using it you may just need to add some simple env vars:

To set up port forwarding:
1. Enable port forwarding in Gluetun by setting `VPN_PORT_FORWARDING=on`
2. Enable Gluetun's control server with `GLUETUN_HTTP_CONTROL_SERVER_ENABLE=on`
3. Configure authentication (API key or Basic Auth)
4. Ensure qSticky has network access to Gluetun's control server

A working Gluetun configuration **might** look like:
```yaml
services:
  gluetun:
    image: qmcgaw/gluetun:latest
    container_name: gluetun
    environment:
      VPN_SERVICE_PROVIDER: protonvpn
      VPN_TYPE: wireguard
      VPN_PORT_FORWARDING: on
      GLUETUN_HTTP_CONTROL_SERVER_ENABLE: on
      WIREGUARD_PRIVATE_KEY: 'YOURKEY'
      SERVER_COUNTRIES: Netherlands
    volumes:
      - ./gluetun/config.toml:/gluetun/auth/config.toml
```

> [!NOTE]  
> Since we are using docker compose networking, port `8000` does not need to be explicitly mapped in docker. If you wish to use the API outside of the docker network, you should map the port.

## 🔄 qSticky Setup
> [!TIP]
> A full list of environment variables are listed and explained below.

To deploy qSticky, add the service to your compose file as so, changing settings as required:
```yaml
services:
  qsticky:
    image: ghcr.io/monstermuffin/qsticky:latest
    container_name: qsticky
    environment:
      # qbittorrent settings
      QBITTORRENT_HOST: gluetun
      QBITTORRENT_HTTPS: false
      QBITTORRENT_PORT: 8080
      QBITTORRENT_USER: admin
      QBITTORRENT_PASS: adminadmin
      # gluetun settings
      GLUETUN_HOST: gluetun
      GLUETUN_AUTH_TYPE: apikey
      GLUETUN_APIKEY: your_api_key_here
      # qSticky settings
      LOG_LEVEL: INFO
    healthcheck:
      test: ["CMD", "python3", "-c", "import json; exit(0 if json.load(open('/app/health/status.json'))['healthy'] else 1)"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: always
```
> [!NOTE]  
> Put qSticky in the same network as gluetun and your host for both gluetun and qBittorrent will be `gluetun`. It is adviced to do this as `container:gluetun` will break the network stack on gluetun restarts.

## 🎮 qBittorrent Setup
qBittorrent can be deployed like the following example:
```yaml
services:
  qbittorrent:
    container_name: qbittorrent
    image: linuxserver/qbittorrent:latest
    network_mode: container:gluetun
    environment:
      PUID: 1000
      PGID: 1000
      TZ: UTC
      WEBUI_PORT: 8080
    volumes:
      - ./qbittorrent/config:/config
      - ./downloads:/downloads
    restart: always
    healthcheck:
      test: ["CMD-SHELL", "curl -sf https://api.ipify.org || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
    depends_on:
      - gluetun
```

> [!NOTE]
> I use the above `healthcheck` to ensure qbittorrent is working. If that check fails, it means qbittorrent can't get out of gluetun's network and marks the container as unhealthy.

## 🧱 Full Stack Example
Here is a complete example stack for deploying Gluetun, qBittorrent and qSticky:

```yaml
services:
  gluetun:
    container_name: gluetun
    image: qmcgaw/gluetun:latest
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun
    environment:
      VPN_SERVICE_PROVIDER: protonvpn
      VPN_TYPE: wireguard
      VPN_PORT_FORWARDING: on
      WIREGUARD_PRIVATE_KEY: 'YOURKEY'
      WIREGUARD_ADDRESSES: 'IP'
      SERVER_COUNTRIES: Netherlands
      GLUETUN_HTTP_CONTROL_SERVER_ENABLE: on
    volumes:
      - ./gluetun/config.toml:/gluetun/auth/config.toml
    ports:
      - 8080:8080  # qBittorrent WebUI
    restart: always

  qbittorrent:
    container_name: qbittorrent
    image: linuxserver/qbittorrent:latest
    network_mode: container:gluetun
    environment:
      PUID: 1000
      PGID: 1000
      TZ: UTC
      WEBUI_PORT: 8080
    volumes:
      - ./qbittorrent/config:/config
      - ./downloads:/downloads
    healthcheck:
      test: ["CMD-SHELL", "curl -sf https://api.ipify.org || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: always
    depends_on:
      - gluetun

  qsticky:
    image: ghcr.io/monstermuffin/qsticky:latest
    container_name: qsticky
    environment:
      # qbittorrent settings
      QBITTORRENT_HOST: gluetun
      QBITTORRENT_HTTPS: false
      QBITTORRENT_PORT: 8080
      QBITTORRENT_USER: admin
      QBITTORRENT_PASS: 'YOURPASS'
      # gluetun settings
      GLUETUN_HOST: gluetun
      GLUETUN_AUTH_TYPE: apikey
      GLUETUN_APIKEY: 'YOURAPIKEY'
      # qSticky settings
      LOG_LEVEL: INFO
    healthcheck:
      test: ["CMD", "python3", "-c", "import json; exit(0 if json.load(open('/app/health/status.json'))['healthy'] else 1)"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: always
```

# ⚡ Configuration
All configuration is done through environment variables:

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| QBITTORRENT_HOST | qBittorrent server hostname | gluetun |
| QBITTORRENT_PORT | qBittorrent server port | 8080 |
| QBITTORRENT_USER | qBittorrent username | admin |
| QBITTORRENT_PASS | qBittorrent password | adminadmin |
| QBITTORRENT_HTTPS | Use HTTPS for qBittorrent connection | false |
| CHECK_INTERVAL | API check interval in seconds | 30 |
| LOG_LEVEL | Logging level (DEBUG, INFO, ERROR, WARNING) | INFO |
| GLUETUN_HOST | Gluetun control server hostname | gluetun |
| GLUETUN_PORT | Gluetun control server port | 8000 |
| GLUETUN_AUTH_TYPE | Gluetun authentication type (basic/apikey) | apikey |
| GLUETUN_USERNAME | Gluetun basic auth username | "" |
| GLUETUN_PASSWORD | Gluetun basic auth password | "" |
| GLUETUN_APIKEY | Gluetun API key | "" |

# 🔍 Verification
To verify qSticky is working:

- Check qSticky logs with `docker logs qsticky`
- Verify qSticky can connect to Gluetun's control server (check logs for API connection messages)
- Confirm the port is being updated in qBittorrent's settings
- Test the Gluetun API endpoint directly using curl (if port 8000 is exposed):
  
  ```bash
  # For API key auth:
  curl -H "X-API-Key: your_api_key" http://localhost:8000/v1/openvpn/portforwarded
  
  # For Basic auth:
  curl -u username:password http://localhost:8000/v1/openvpn/portforwarded
  ```

When successful, the logs will look something like:

```bash
qsticky - INFO - Starting qSticky port manager...
qsticky - INFO - Port change needed: 54219 -> 45720
qsticky - INFO - Successfully updated port to 45720
qsticky - INFO - Initial status - Gluetun: ✓, qBit: ✓, Port: 45720
```

# 💓 Health Monitoring
qSticky includes Docker health checks and maintains a health status file at `/app/health/status.json`. The health status includes:
- Overall health status
- Uptime
- Last check timestamp
- Last port change time
- Current port
- Last error (if any)*

The Docker container will be marked as unhealthy if:

- The application fails to write health status
- qBittorrent becomes unreachable
- Port updates fail repeatedly
- Other errors occur
