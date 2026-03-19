import json
import secrets
import shutil
import socket
import subprocess
import time
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any

import psutil

try:
    import docker
    from docker.errors import DockerException, ImageNotFound, NotFound
except ImportError:  # pragma: no cover
    docker = None
    DockerException = Exception
    ImageNotFound = Exception
    NotFound = Exception


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
BASE_IMAGES_DIR = DATA_DIR / "base_images"
VMS_DIR = DATA_DIR / "vms"
STATE_DIR = DATA_DIR / "state"
STATE_FILE = STATE_DIR / "instances.json"
EVENTS_FILE = STATE_DIR / "monitor_events.log"
DOCKERFILES_DIR = PROJECT_ROOT / "dockerfiles"

MOSCOW_TZ = timezone(timedelta(hours=3), name="MSK")

for directory in (DATA_DIR, BASE_IMAGES_DIR, VMS_DIR, STATE_DIR):
    directory.mkdir(parents=True, exist_ok=True)


VM_OS_CONFIG: dict[str, dict[str, str]] = {
    "ubuntu-22.04": {
        "title": "Ubuntu 22.04 cloud image",
        "file": "ubuntu-22.04.img",
        "url": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
    },
    "debian-12": {
        "title": "Debian 12 cloud image",
        "file": "debian-12.qcow2",
        "url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2",
    },
}

CONTAINER_OS_CONFIG: dict[str, dict[str, str]] = {
    "ubuntu-22.04": {
        "title": "Ubuntu 22.04 container",
        "dockerfile_dir": str(DOCKERFILES_DIR / "ubuntu"),
        "tag": "acos-lab3-ubuntu-ssh:latest",
    },
    "debian-12": {
        "title": "Debian 12 container",
        "dockerfile_dir": str(DOCKERFILES_DIR / "debian"),
        "tag": "acos-lab3-debian-ssh:latest",
    },
}


def now_msk() -> datetime:
    return datetime.now(MOSCOW_TZ)


def iso_now() -> str:
    return now_msk().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def generate_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3)}"


def generate_password() -> str:
    return secrets.token_urlsafe(9)


def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def ensure_command_exists(command: str) -> None:
    if shutil.which(command) is None:
        raise RuntimeError(f"Command '{command}' was not found in PATH")


def wait_tcp_open(host: str, port: int, timeout_seconds: int = 90) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(2)
    return False


def probe_ssh_auth_stage(host: str, port: int, username: str, timeout_seconds: int = 10) -> bool:
    ensure_command_exists("ssh")

    command = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "PreferredAuthentications=password",
        "-o",
        "PubkeyAuthentication=no",
        "-o",
        "NumberOfPasswordPrompts=0",
        "-o",
        f"ConnectTimeout={timeout_seconds}",
        "-p",
        str(port),
        f"{username}@{host}",
        "exit",
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 2,
        )
    except subprocess.TimeoutExpired:
        return False

    combined = f"{result.stdout}\n{result.stderr}".lower()

    if result.returncode == 0:
        return True

    if "permission denied" in combined:
        return True

    not_ready_markers = [
        "connection reset by peer",
        "connection refused",
        "timed out",
        "banner exchange",
        "kex_exchange_identification",
        "no route to host",
        "operation timed out",
    ]
    if any(marker in combined for marker in not_ready_markers):
        return False

    return False


def wait_ssh_ready(
    host: str,
    port: int,
    username: str,
    timeout_seconds: int = 300,
    consecutive_successes: int = 2,
) -> bool:
    deadline = time.time() + timeout_seconds
    success_count = 0

    while time.time() < deadline:
        if not wait_tcp_open(host, port, timeout_seconds=5):
            success_count = 0
            time.sleep(2)
            continue

        if probe_ssh_auth_stage(host, port, username=username, timeout_seconds=5):
            success_count += 1
            if success_count >= consecutive_successes:
                time.sleep(2)
                return True
        else:
            success_count = 0

        time.sleep(2)

    return False


def build_ssh_command(username: str, port: int) -> str:
    return (
        "ssh "
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null "
        "-o ConnectTimeout=5 "
        f"{username}@localhost -p {port}"
    )


class StateStore:
    def __init__(self, path: Path = STATE_FILE) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        if not self.path.exists():
            self.save({})

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _save_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        temp_path.replace(self.path)

    def load(self) -> dict[str, Any]:
        with self._lock:
            return self._load_unlocked()

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._save_unlocked(data)

    def get(self, instance_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._load_unlocked().get(instance_id)

    def upsert(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._load_unlocked()
            data[record["id"]] = record
            self._save_unlocked(data)
        return record

    def delete(self, instance_id: str) -> None:
        with self._lock:
            data = self._load_unlocked()
            data.pop(instance_id, None)
            self._save_unlocked(data)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._load_unlocked().values())


class EventLog:
    def __init__(self, path: Path = EVENTS_FILE) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, message: str) -> None:
        line = f"[{iso_now()}] {message}\n"
        with self.path.open("a", encoding="utf-8") as file:
            file.write(line)

    def tail(self, limit: int = 50) -> list[str]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as file:
            lines = file.readlines()
        return [line.rstrip("\n") for line in lines[-limit:]]


class VMManager:
    def __init__(self, store: StateStore, events: EventLog) -> None:
        self.store = store
        self.events = events

    def ensure_base_image(self, os_name: str) -> Path:
        if os_name not in VM_OS_CONFIG:
            raise RuntimeError(f"Unknown VM OS: {os_name}")
        config = VM_OS_CONFIG[os_name]
        path = BASE_IMAGES_DIR / config["file"]
        if not path.exists():
            urllib.request.urlretrieve(config["url"], path)
            self.events.write(f"Base image downloaded for {os_name}: {path.name}")
        return path

    def get_image_format(self, image_path: Path) -> str:
        ensure_command_exists("qemu-img")
        result = subprocess.run(
            ["qemu-img", "info", str(image_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines():
            if "file format" in line:
                return line.split(":", 1)[1].strip()
        return "qcow2"

    def create_seed_iso(self, instance_dir: Path, username: str, password: str, hostname: str) -> Path:
        ensure_command_exists("genisoimage")
        user_data = f"""#cloud-config
users:
  - name: {username}
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: false
    plain_text_passwd: '{password}'
ssh_pwauth: true
chpasswd:
  expire: false
hostname: {hostname}
"""
        meta_data = f"instance-id: {hostname}\nlocal-hostname: {hostname}\n"
        user_data_path = instance_dir / "user-data"
        meta_data_path = instance_dir / "meta-data"
        seed_path = instance_dir / "seed.iso"
        user_data_path.write_text(user_data, encoding="utf-8")
        meta_data_path.write_text(meta_data, encoding="utf-8")
        subprocess.run(
            [
                "genisoimage",
                "-output",
                str(seed_path),
                "-volid",
                "cidata",
                "-joliet",
                "-rock",
                str(user_data_path),
                str(meta_data_path),
            ],
            capture_output=True,
            check=True,
        )
        return seed_path

    def create_disk(self, base_image: Path, target_disk: Path, disk_size_gb: int) -> None:
        ensure_command_exists("qemu-img")
        image_format = self.get_image_format(base_image)
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                image_format,
                "-b",
                str(base_image),
                str(target_disk),
                f"{disk_size_gb}G",
            ],
            capture_output=True,
            check=True,
        )

    def _qemu_command(self, record: dict[str, Any]) -> list[str]:
        ensure_command_exists("qemu-system-x86_64")
        command = [
            "qemu-system-x86_64",
            "-m",
            str(record["ram_mb"]),
            "-smp",
            str(record["cpu"]),
            "-drive",
            f"file={record['disk_path']},format=qcow2,if=virtio",
            "-drive",
            f"file={record['seed_path']},format=raw,media=cdrom",
            "-netdev",
            f"user,id=net0,hostfwd=tcp:127.0.0.1:{record['ssh_port']}-:22",
            "-device",
            "virtio-net-pci,netdev=net0",
            "-nographic",
        ]
        if Path("/dev/kvm").exists():
            command.insert(1, "-enable-kvm")
        return command

    def _start_vm_process(self, record: dict[str, Any]) -> dict[str, Any]:
        instance_dir = Path(record["instance_dir"])
        log_path = instance_dir / "qemu.log"
        with log_path.open("a", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                self._qemu_command(record),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=instance_dir,
            )
        record["pid"] = process.pid
        record["status"] = "starting"
        record["updated_at"] = iso_now()
        record["last_error"] = ""
        self.store.upsert(record)
        self.events.write(f"VM {record['id']} started (port {record['ssh_port']})")
        return record

    def _finalize_vm_start(self, instance_id: str, timeout_seconds: int = 900) -> None:
        initial_record = self.store.get(instance_id)
        if not initial_record:
            return

        is_ready = wait_ssh_ready(
            "127.0.0.1",
            int(initial_record["ssh_port"]),
            username=initial_record["username"],
            timeout_seconds=timeout_seconds,
            consecutive_successes=2,
        )

        record = self.store.get(instance_id)
        if not record:
            return

        if is_ready:
            record["status"] = "running"
            record["last_error"] = ""
            record["updated_at"] = iso_now()
            self.store.upsert(record)
            self.events.write(f"VM {instance_id} is SSH-ready")
            return

        pid = record.get("pid")
        if pid and psutil.pid_exists(pid):
            record["status"] = "error"
            record["last_error"] = "VM did not become SSH-ready within the timeout"
            record["updated_at"] = iso_now()
            self.store.upsert(record)
            self.events.write(f"VM {instance_id} startup timed out")
        else:
            # VM may have been stopped or deleted while the background checker was running.
            record = self.store.get(instance_id)
            if not record:
                return
            if record["status"] != "stopped":
                record["status"] = "error"
                record["last_error"] = "VM process exited during startup"
                record["updated_at"] = iso_now()
                self.store.upsert(record)
                self.events.write(f"VM {instance_id} exited during startup")

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        instance_id = generate_id("vm")
        instance_dir = VMS_DIR / instance_id
        instance_dir.mkdir(parents=True, exist_ok=True)

        username = "tenant"
        password = generate_password()
        ssh_port = find_free_port()
        base_image = self.ensure_base_image(payload["os_name"])
        disk_path = instance_dir / "root.qcow2"
        seed_path = self.create_seed_iso(instance_dir, username, password, instance_id)
        self.create_disk(base_image, disk_path, int(payload["disk_gb"]))

        expires_at = None
        if payload.get("lifetime_minutes"):
            expires_at = (now_msk() + timedelta(minutes=int(payload["lifetime_minutes"]))).isoformat()

        record: dict[str, Any] = {
            "id": instance_id,
            "type": "vm",
            "client_name": payload["client_name"],
            "os_name": payload["os_name"],
            "cpu": int(payload["cpu"]),
            "ram_mb": int(payload["ram_mb"]),
            "disk_gb": int(payload["disk_gb"]),
            "max_cpu_seconds": int(payload.get("max_cpu_seconds") or 0),
            "cpu_seconds": 0.0,
            "created_at": iso_now(),
            "updated_at": iso_now(),
            "expires_at": expires_at,
            "status": "created",
            "username": username,
            "password": password,
            "ssh_port": ssh_port,
            "ssh_command": build_ssh_command(username, ssh_port),
            "instance_dir": str(instance_dir),
            "disk_path": str(disk_path),
            "seed_path": str(seed_path),
            "base_image_path": str(base_image),
            "pid": None,
            "last_stop_reason": "",
            "last_error": "",
        }
        self.store.upsert(record)
        started_record = self._start_vm_process(record)
        Thread(target=self._finalize_vm_start, args=(instance_id,), daemon=True).start()
        return started_record

    def start(self, instance_id: str) -> dict[str, Any]:
        record = self.store.get(instance_id)
        if not record:
            raise RuntimeError("VM was not found")
        if record["status"] in {"running", "starting"}:
            return record

        started_record = self._start_vm_process(record)
        Thread(target=self._finalize_vm_start, args=(instance_id,), daemon=True).start()
        return started_record

    def stop(self, instance_id: str, reason: str = "manual stop") -> dict[str, Any]:
        record = self.store.get(instance_id)
        if not record:
            raise RuntimeError("VM was not found")
        pid = record.get("pid")
        if pid and psutil.pid_exists(pid):
            process = psutil.Process(pid)
            process.terminate()
            try:
                process.wait(timeout=10)
            except psutil.TimeoutExpired:
                process.kill()
        elif record.get("disk_path"):
            subprocess.run(["pkill", "-f", record["disk_path"]], capture_output=True)
        record["pid"] = None
        record["status"] = "stopped"
        record["last_stop_reason"] = reason
        record["updated_at"] = iso_now()
        self.store.upsert(record)
        self.events.write(f"VM {instance_id} stopped ({reason})")
        return record

    def delete(self, instance_id: str) -> None:
        record = self.store.get(instance_id)
        if not record:
            return
        if record["status"] in {"running", "starting"}:
            self.stop(instance_id, reason="delete")
        instance_dir = Path(record["instance_dir"])
        if instance_dir.exists():
            shutil.rmtree(instance_dir)
        self.store.delete(instance_id)
        self.events.write(f"VM {instance_id} deleted")

    def sync(self, record: dict[str, Any]) -> dict[str, Any]:
        pid = record.get("pid")
        if pid and psutil.pid_exists(pid):
            process = psutil.Process(pid)
            cpu_times = process.cpu_times()
            record["cpu_seconds"] = round(float(cpu_times.user + cpu_times.system), 2)
            if record.get("status") not in {"starting", "running"}:
                record["status"] = "starting"
        elif record.get("status") in {"running", "starting"}:
            record["pid"] = None
            if record["status"] != "error":
                record["status"] = "stopped"

        record["updated_at"] = iso_now()
        self.store.upsert(record)
        return record


class ContainerManager:
    def __init__(self, store: StateStore, events: EventLog) -> None:
        self.store = store
        self.events = events

    def _docker_client(self):
        if docker is None:
            raise RuntimeError("Python package 'docker' is not installed")
        try:
            return docker.from_env()
        except DockerException as exc:  # pragma: no cover
            raise RuntimeError(f"Could not connect to Docker daemon: {exc}") from exc

    def ensure_image(self, os_name: str) -> str:
        if os_name not in CONTAINER_OS_CONFIG:
            raise RuntimeError(f"Unknown container OS: {os_name}")
        client = self._docker_client()
        config = CONTAINER_OS_CONFIG[os_name]
        tag = config["tag"]
        try:
            client.images.get(tag)
            return tag
        except ImageNotFound:
            image, _ = client.images.build(path=config["dockerfile_dir"], tag=tag)
            self.events.write(f"Container image built: {tag}")
            return tag

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._docker_client()
        image_tag = self.ensure_image(payload["os_name"])
        instance_id = generate_id("ct")
        username = "tenant"
        password = generate_password()
        ssh_port = find_free_port()
        container_name = f"{instance_id}-ssh"
        expires_at = None
        if payload.get("lifetime_minutes"):
            expires_at = (now_msk() + timedelta(minutes=int(payload["lifetime_minutes"]))).isoformat()

        container = client.containers.run(
            image_tag,
            name=container_name,
            detach=True,
            environment={
                "TENANT_LOGIN": username,
                "TENANT_PASSWORD": password,
            },
            nano_cpus=int(payload["cpu"]) * 1_000_000_000,
            mem_limit=f"{int(payload['ram_mb'])}m",
            ports={"22/tcp": ("127.0.0.1", ssh_port)},
        )
        container.reload()
        pid = int(container.attrs.get("State", {}).get("Pid", 0) or 0)

        record: dict[str, Any] = {
            "id": instance_id,
            "type": "container",
            "client_name": payload["client_name"],
            "os_name": payload["os_name"],
            "cpu": int(payload["cpu"]),
            "ram_mb": int(payload["ram_mb"]),
            "disk_gb": 0,
            "max_cpu_seconds": int(payload.get("max_cpu_seconds") or 0),
            "cpu_seconds": 0.0,
            "created_at": iso_now(),
            "updated_at": iso_now(),
            "expires_at": expires_at,
            "status": "running",
            "username": username,
            "password": password,
            "ssh_port": ssh_port,
            "ssh_command": build_ssh_command(username, ssh_port),
            "container_name": container_name,
            "docker_image": image_tag,
            "pid": pid,
            "last_stop_reason": "",
            "last_error": "",
        }
        self.store.upsert(record)
        wait_ssh_ready("127.0.0.1", ssh_port, username=username, timeout_seconds=60, consecutive_successes=1)
        self.events.write(f"Container {instance_id} started")
        return record

    def start(self, instance_id: str) -> dict[str, Any]:
        record = self.store.get(instance_id)
        if not record:
            raise RuntimeError("Container was not found")
        client = self._docker_client()
        container = client.containers.get(record["container_name"])
        container.start()
        container.reload()
        record["pid"] = int(container.attrs.get("State", {}).get("Pid", 0) or 0)
        record["status"] = "running"
        record["updated_at"] = iso_now()
        self.store.upsert(record)
        self.events.write(f"Container {instance_id} started")
        return record

    def stop(self, instance_id: str, reason: str = "manual stop") -> dict[str, Any]:
        record = self.store.get(instance_id)
        if not record:
            raise RuntimeError("Container was not found")
        client = self._docker_client()
        try:
            container = client.containers.get(record["container_name"])
            container.stop(timeout=5)
        except NotFound:
            pass
        record["status"] = "stopped"
        record["pid"] = None
        record["last_stop_reason"] = reason
        record["updated_at"] = iso_now()
        self.store.upsert(record)
        self.events.write(f"Container {instance_id} stopped ({reason})")
        return record

    def delete(self, instance_id: str) -> None:
        record = self.store.get(instance_id)
        if not record:
            return
        client = self._docker_client()
        try:
            container = client.containers.get(record["container_name"])
            container.remove(force=True)
        except NotFound:
            pass
        self.store.delete(instance_id)
        self.events.write(f"Container {instance_id} deleted")

    def sync(self, record: dict[str, Any]) -> dict[str, Any]:
        client = self._docker_client()
        try:
            container = client.containers.get(record["container_name"])
        except NotFound:
            record["status"] = "deleted"
            record["pid"] = None
            record["updated_at"] = iso_now()
            self.store.upsert(record)
            return record

        container.reload()
        state = container.attrs.get("State", {})
        record["pid"] = int(state.get("Pid", 0) or 0)
        if state.get("Running"):
            record["status"] = "running"
            if record["pid"] and psutil.pid_exists(record["pid"]):
                process = psutil.Process(record["pid"])
                cpu_times = process.cpu_times()
                record["cpu_seconds"] = round(float(cpu_times.user + cpu_times.system), 2)
        else:
            record["status"] = "stopped"
        record["updated_at"] = iso_now()
        self.store.upsert(record)
        return record


class PlatformService:
    def __init__(self) -> None:
        self.store = StateStore()
        self.events = EventLog()
        self.vm = VMManager(self.store, self.events)
        self.container = ContainerManager(self.store, self.events)

    def catalog(self) -> dict[str, Any]:
        return {
            "vm": VM_OS_CONFIG,
            "container": CONTAINER_OS_CONFIG,
        }

    def create_vm(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.vm.create(payload)

    def create_container(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.container.create(payload)

    def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        return self.store.get(instance_id)

    def list_instances(self, client_name: str | None = None, include_all: bool = False) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for record in self.store.list():
            synced = self.sync_record(record)
            if include_all or not client_name or synced.get("client_name") == client_name:
                records.append(synced)
        records.sort(key=lambda item: item["created_at"], reverse=True)
        return records

    def sync_record(self, record: dict[str, Any]) -> dict[str, Any]:
        if record["type"] == "vm":
            return self.vm.sync(record)
        return self.container.sync(record)

    def stop(self, instance_id: str, reason: str = "manual stop") -> dict[str, Any]:
        record = self.store.get(instance_id)
        if not record:
            raise RuntimeError("Instance was not found")
        if record["type"] == "vm":
            return self.vm.stop(instance_id, reason=reason)
        return self.container.stop(instance_id, reason=reason)

    def start(self, instance_id: str) -> dict[str, Any]:
        record = self.store.get(instance_id)
        if not record:
            raise RuntimeError("Instance was not found")
        if record["type"] == "vm":
            return self.vm.start(instance_id)
        return self.container.start(instance_id)

    def delete(self, instance_id: str) -> None:
        record = self.store.get(instance_id)
        if not record:
            return
        if record["type"] == "vm":
            self.vm.delete(instance_id)
        else:
            self.container.delete(instance_id)

    def monitor_once(self) -> list[dict[str, Any]]:
        updated_records: list[dict[str, Any]] = []
        for record in self.store.list():
            try:
                synced = self.sync_record(record)
                updated_records.append(synced)

                if synced["status"] not in {"running", "starting"}:
                    continue

                expires_at = parse_iso(synced.get("expires_at"))
                if expires_at and now_msk() >= expires_at.astimezone(MOSCOW_TZ):
                    self.stop(synced["id"], reason="lifetime expired")
                    continue

                max_cpu_seconds = int(synced.get("max_cpu_seconds") or 0)
                if max_cpu_seconds > 0 and float(synced.get("cpu_seconds") or 0.0) >= max_cpu_seconds:
                    self.stop(synced["id"], reason="cpu time limit reached")
            except Exception as exc:  # pragma: no cover
                record["status"] = "error"
                record["last_error"] = str(exc)
                record["updated_at"] = iso_now()
                self.store.upsert(record)
                self.events.write(f"Monitor error for {record['id']}: {exc}")
                updated_records.append(record)

        return updated_records

    def tail_events(self, limit: int = 50) -> list[str]:
        return self.events.tail(limit=limit)
