# redis-ha-sample

A FastAPI service backed by Redis in high-availability mode with **Redis Sentinel**, all running under `docker compose`.

```
            ┌──────────────┐
  :8000 ──▶ │  api (FastAPI)│── asks sentinels "who is master?"
            └──────┬───────┘
     ┌─────────────┼─────────────┐
 sentinel-1    sentinel-2    sentinel-3      quorum = 2
     │             │             │
     ▼             ▼             ▼
 redis-master ──▶ redis-replica-1, redis-replica-2
 10.77.0.10        10.77.0.11       10.77.0.12
```

- 1 master + 2 replicas, AOF enabled
- 3 sentinels, quorum 2, master marked down after 5s
- The API uses `redis.asyncio.sentinel`. Writes go to the master and reads can go to a replica. Clients retry with exponential backoff, so a request made during a failover waits for the new master instead of failing.

## Run

```bash
docker compose up -d --build --wait
```

API docs: http://localhost:8000/docs

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Ping the current master |
| GET | `/ha` | Current master and replicas, as reported by Sentinel |
| PUT | `/kv/{key}` | Body `{"value": "...", "ttl": 60}` (`ttl` is optional) |
| GET | `/kv/{key}?from_replica=true` | Read from the master (default) or a replica |
| DELETE | `/kv/{key}` | Delete a key |
| POST | `/counter/{name}/incr` | `INCR` a counter |

If Redis can't be reached, the API returns `503`.

## Failover demo

```bash
curl -X PUT localhost:8000/kv/foo -H 'content-type: application/json' -d '{"value":"bar"}'
curl localhost:8000/ha            # master: 10.77.0.10

docker compose stop redis-master

# This write blocks for about 5s while Sentinel promotes a replica, then succeeds
curl -X PUT localhost:8000/kv/during -H 'content-type: application/json' -d '{"value":"failover"}'
curl localhost:8000/ha            # master: 10.77.0.11 or 10.77.0.12
curl localhost:8000/kv/foo        # still "bar"

docker compose start redis-master # the old master rejoins as a replica
```

## Why static IPs?

Sentinel is pointed at fixed container IPs, not Docker hostnames. When a container stops, Docker removes its DNS name. If Sentinel is set up with `resolve-hostnames`, each lookup of the missing name then blocks for several seconds. The sentinels drop into TILT mode and **never fail over**. Static IPs on a fixed subnet (`10.77.0.0/24`) keep DNS off the failover path.

## Clean up

```bash
docker compose down -v
```
