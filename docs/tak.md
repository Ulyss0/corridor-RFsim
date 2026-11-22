# TAK side: OpenTAKServer, CoT client, ATAK

Corridor speaks CoT (Cursor on Target) to an OpenTAKServer (OTS); ATAK phones connect to the
same OTS. Requirements and development setup: [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Start OTS

```bash
cd infra/ots
cp ots_config.env.example ots_config.env
docker compose up -d
docker compose ps        # ots must become "healthy" (about 1 minute on first start)
docker compose logs -f ots
```

[`infra/ots/compose.yml`](../infra/ots/compose.yml) is a trimmed copy of `brian7704/OpenTAKServer-Docker@30aa20b`. The header
of the file lists what was removed and why (no web UI, no video for now).

Images are pinned by version tag and digest: OTS 1.8.3 (2026-11-11). Do not use upstream
`:master`: it is a stale 1.5.14 build from 2025-10 that loses RabbitMQ bindings at random.
To upgrade: change the version tag in `compose.yml`, `docker compose pull`, run the smoke test
(several times, including after `docker compose up -d --force-recreate`), then re-pin with:

```bash
for img in $(docker compose config --images); do
  docker image inspect --format '{{index .RepoDigests 0}}' "$img"
done
```

First start takes 1 to 2 minutes (CA creation, database migrations). The one-shot `ots-init`
service gives the OTS user write access to the data volume, then exits.

The OTS API is published on `127.0.0.1:8081` only: `curl http://127.0.0.1:8081/api/health`.

Reset everything (deletes the OTS database and CA): `docker compose down -v`.

## TAK CLI (`corridor-tak`)

```bash
cd backend
uv sync
uv run corridor-tak smoke     # two connections through OTS, exit 0 if demo shapes are routed
uv run corridor-tak demo      # publish a test jam zone + corridor, refreshed every 30 s
uv run corridor-tak listen    # print every CoT routed to us
uv run corridor-tak listen --capture captures/   # also save raw XML
```

Options: `--host` (default 127.0.0.1), `--port` (8088), `--lat` / `--lon` (demo position).

## Connect ATAK

1. Phone and laptop on the same network (Wi-Fi LAN, or the same Tailscale tailnet).
2. If a firewall runs on the laptop, open TCP 8088.
3. ATAK: Settings > Network Preferences > TAK Servers > Add. Address: laptop IP, port 8088,
   protocol TCP, no SSL.
4. From `backend/`, run `uv run corridor-tak demo --lat <lat> --lon <lon>` near where the map is centered: a red
   jam zone, a blue corridor crossing it and an EW-1 marker appear.

## Shape format: check against a real capture

`backend/src/corridor/tak/cot.py` builds shapes in the layout ATAK uses for drawn shapes (`u-d-f`, one
`<link point>` per vertex, signed ARGB colours). It was written from known ATAK output, not from
a schema. Before building on it:

1. From `backend/`: `uv run corridor-tak listen --capture captures/`
2. In ATAK, draw a polygon and a line and give them a name.
3. Compare `captures/*u-d-*.xml` with what `shape()` produces and align.

## OTS notes (checked in the running OTS 1.8.3 code)

- A TCP client is registered, and only then receives other clients' CoT, after it sends an
  event carrying `<contact>`. `TakClient` sends a self SA on connect and every 30 s.
- Race in OTS: if that first SA arrives before the handler's RabbitMQ channel is open, OTS
  records the client but never binds its queues. The client can send but receives nothing,
  and nothing is logged. `TakClient` waits 1 s (`CONNECT_SETTLE_S`) before its first send
  (smoke test: 3/3 failures without the delay, 6/6 passes with it). ATAK can hit the same
  race: if it shows its own position but none of Corridor's shapes, disconnect and reconnect
  the server in ATAK.
- OTS does not echo a client's own CoT back to it.
- The OTS CA is created on first start; default admin account: `administrator` / `password`.
- Every CoT is also published to the RabbitMQ `firehose` exchange: a later option for
  consuming everything without a TCP connection.
