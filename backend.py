from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from platform_service import PlatformService

app = FastAPI(title="ACOS Lab 3", version="1.0.0")
service = PlatformService()


class CreateVMRequest(BaseModel):
    client_name: str = Field(min_length=1, max_length=50)
    os_name: str
    cpu: int = Field(ge=1, le=10)
    ram_mb: int = Field(ge=512, le=4096)
    disk_gb: int = Field(ge=5, le=50)
    lifetime_minutes: int | None = Field(default=15, ge=1, le=1440)
    max_cpu_seconds: int | None = Field(default=0, ge=0, le=86400)


class CreateContainerRequest(BaseModel):
    client_name: str = Field(min_length=1, max_length=50)
    os_name: str
    cpu: int = Field(ge=1, le=10)
    ram_mb: int = Field(ge=128, le=4096)
    lifetime_minutes: int | None = Field(default=15, ge=1, le=1440)
    max_cpu_seconds: int | None = Field(default=0, ge=0, le=86400)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/catalog")
def catalog() -> dict:
    return service.catalog()


@app.post("/instances/vm")
def create_vm(request: CreateVMRequest) -> dict:
    try:
        return service.create_vm(request.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/instances/container")
def create_container(request: CreateContainerRequest) -> dict:
    try:
        return service.create_container(request.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/instances")
def list_instances(
    client_name: str | None = Query(default=None),
    include_all: bool = Query(default=False),
) -> list[dict]:
    try:
        return service.list_instances(client_name=client_name, include_all=include_all)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/instances/{instance_id}")
def get_instance(instance_id: str) -> dict:
    record = service.get_instance(instance_id)
    if not record:
        raise HTTPException(status_code=404, detail="Instance was not found")
    return service.sync_record(record)


@app.post("/instances/{instance_id}/action")
def instance_action(instance_id: str, action: Literal["start", "stop"]) -> dict:
    try:
        if action == "start":
            return service.start(instance_id)
        return service.stop(instance_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/instances/{instance_id}")
def delete_instance(instance_id: str) -> dict[str, str]:
    try:
        service.delete(instance_id)
        return {"status": "deleted"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/monitor/events")
def monitor_events(limit: int = Query(default=20, ge=1, le=200)) -> list[str]:
    return service.tail_events(limit)


@app.post("/monitor/run-once")
def monitor_run_once() -> list[dict]:
    return service.monitor_once()
