import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from redis.asyncio.retry import Retry
from redis.asyncio.sentinel import Sentinel
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError, RedisError, TimeoutError

SENTINELS = [
    (host, int(port))
    for host, port in (
        s.split(":")
        for s in os.getenv(
            "SENTINELS", "sentinel-1:26379,sentinel-2:26379,sentinel-3:26379"
        ).split(",")
    )
]
MASTER_NAME = os.getenv("MASTER_NAME", "mymaster")


@asynccontextmanager
async def lifespan(app: FastAPI):
    sentinel = Sentinel(SENTINELS, socket_timeout=0.5)
    client_kwargs = dict(
        socket_timeout=1.0,
        decode_responses=True,
        # Retries re-resolve the master through sentinel, so writes survive a failover.
        retry=Retry(ExponentialBackoff(cap=2, base=0.1), 10),
        retry_on_error=[ConnectionError, TimeoutError],
    )
    app.state.sentinel = sentinel
    app.state.master = sentinel.master_for(MASTER_NAME, **client_kwargs)
    app.state.replica = sentinel.slave_for(MASTER_NAME, **client_kwargs)
    yield
    await app.state.master.aclose()
    await app.state.replica.aclose()


app = FastAPI(title="Redis Sentinel HA sample", lifespan=lifespan)


@app.exception_handler(RedisError)
async def redis_error_handler(request: Request, exc: RedisError):
    return JSONResponse(status_code=503, content={"detail": f"redis unavailable: {exc}"})


class Item(BaseModel):
    value: str
    ttl: int | None = None


@app.get("/health")
async def health():
    return {"redis": await app.state.master.ping()}


@app.get("/ha")
async def ha():
    sentinel: Sentinel = app.state.sentinel
    master = await sentinel.discover_master(MASTER_NAME)
    replicas = await sentinel.discover_slaves(MASTER_NAME)
    return {
        "master": f"{master[0]}:{master[1]}",
        "replicas": [f"{h}:{p}" for h, p in replicas],
    }


@app.put("/kv/{key}")
async def put_key(key: str, item: Item):
    await app.state.master.set(key, item.value, ex=item.ttl)
    return {"key": key, "value": item.value, "ttl": item.ttl}


@app.get("/kv/{key}")
async def get_key(key: str, from_replica: bool = False):
    client = app.state.replica if from_replica else app.state.master
    value = await client.get(key)
    if value is None:
        raise HTTPException(status_code=404, detail="key not found")
    return {"key": key, "value": value, "source": "replica" if from_replica else "master"}


@app.delete("/kv/{key}")
async def delete_key(key: str):
    return {"key": key, "deleted": bool(await app.state.master.delete(key))}


@app.post("/counter/{name}/incr")
async def incr(name: str):
    return {"name": name, "value": await app.state.master.incr(f"counter:{name}")}
