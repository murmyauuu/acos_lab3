from datetime import datetime, timedelta, timezone
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

API_URL = "http://127.0.0.1:8000"
TIMEOUT = 300
MOSCOW_TZ = timezone(timedelta(hours=3), name="MSK")

st.set_page_config(page_title="ACOS Lab 3", layout="wide")
st.title("Monitor and Instance List")

if "monitor_message" not in st.session_state:
    st.session_state["monitor_message"] = ""


def api_get(path: str, **params):
    return requests.get(f"{API_URL}{path}", params=params, timeout=TIMEOUT)

def api_post(path: str, **kwargs):
    return requests.post(f"{API_URL}{path}", timeout=TIMEOUT, **kwargs)

def api_delete(path: str):
    return requests.delete(f"{API_URL}{path}", timeout=TIMEOUT)

def load_catalog():
    response = api_get("/catalog")
    response.raise_for_status()
    return response.json()

def load_instances(client_name: str, include_all: bool):
    response = api_get("/instances", client_name=client_name, include_all=include_all)
    response.raise_for_status()
    return response.json()

def load_events(limit: int = 20):
    response = api_get("/monitor/events", limit=limit)
    response.raise_for_status()
    return response.json()

def create_vm(payload: dict):
    response = api_post("/instances/vm", json=payload)
    response.raise_for_status()
    return response.json()

def create_container(payload: dict):
    response = api_post("/instances/container", json=payload)
    response.raise_for_status()
    return response.json()

def instance_action(instance_id: str, action: str):
    response = api_post(f"/instances/{instance_id}/action", params={"action": action})
    response.raise_for_status()
    return response.json()


def delete_instance(instance_id: str):
    response = api_delete(f"/instances/{instance_id}")
    response.raise_for_status()
    return response.json()

def run_monitor_once():
    response = api_post("/monitor/run-once")
    response.raise_for_status()
    return response.json()

def format_timestamp(value: str | None) -> str:
    if not value:
        return "no limit"
    try:
        dt = datetime.fromisoformat(value)
        dt = dt.astimezone(MOSCOW_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S MSK")
    except Exception:
        return value


def format_event_line(line: str) -> str:
    line = line.strip()
    if not line.startswith("[") or "] " not in line:
        return line

    timestamp_part, message_part = line.split("] ", 1)
    timestamp_raw = timestamp_part[1:]

    try:
        dt = datetime.fromisoformat(timestamp_raw)
        dt = dt.astimezone(MOSCOW_TZ)
        formatted_ts = dt.strftime("%Y-%m-%d %H:%M:%S MSK")
        return f"[{formatted_ts}] {message_part}"
    except Exception:
        return line


def format_cpu_minutes(cpu_seconds: float | int | None) -> str:
    seconds = float(cpu_seconds or 0.0)
    minutes = seconds / 60.0
    return f"{minutes:.2f} min"

def render_status(status: str):
    if status == "starting":
        st.warning("SSH is not ready yet")
    elif status == "running":
        st.success("SSH is available")
    elif status == "stopped":
        st.info("Stopped")
    elif status == "error":
        st.error("Startup error")
    elif status == "deleted":
        st.error("Deleted")
    else:
        st.info(status)


def render_instance_card(item: dict):
    with st.container(border=True):
        top_cols = st.columns([3, 1, 1, 1])

        top_cols[0].subheader(item["id"])
        top_cols[1].write(f"Type: {item['type']}")
        top_cols[2].write(f"Status: {item['status']}")
        top_cols[3].write(f"Client: {item.get('client_name', '-')}")
        render_status(item["status"])

        left, middle, right = st.columns(3)

        with left:
            st.write(f"OS: {item.get('os_name', '-')}")
            st.write(f"CPU: {item.get('cpu', 0)}")
            st.write(f"RAM: {item.get('ram_mb', 0)} MB")
            if item["type"] == "vm":
                st.write(f"Disk: {item.get('disk_gb', 0)} GB")

        with middle:
            st.write(f"SSH port: {item.get('ssh_port', '-')}")
            st.code(item.get("ssh_command", "-"), language="bash")
            st.caption('Connect only after the status becomes "running".')
            st.write(f"Login: {item.get('username', '-')}")
            st.write(f"Password: {item.get('password', '-')}")

        with right:
            st.write(f"Last stop reason: {item.get('last_stop_reason') or '-'}")
            if item.get("last_error"):
                st.error(item["last_error"])

        limits_cols = st.columns(3)
        limits_cols[0].write(f"CPU time: {format_cpu_minutes(item.get('cpu_seconds', 0))}")
        cpu_limit_seconds = int(item.get("max_cpu_seconds", 0) or 0)
        cpu_limit_minutes = cpu_limit_seconds / 60.0
        limits_cols[1].write(f"CPU limit: {cpu_limit_minutes:.2f} min")
        limits_cols[2].write(f"Expires at: {format_timestamp(item.get('expires_at'))}")

        action_cols = st.columns(3)

        start_disabled = item["status"] in {"running", "starting", "deleted"}
        stop_disabled = item["status"] in {"stopped", "deleted"}
        delete_disabled = False

        if action_cols[0].button("Start", key=f"start-{item['id']}", disabled=start_disabled):
            try:
                instance_action(item["id"], "start")
                st.rerun()
            except requests.RequestException as exc:
                st.error(f"Could not start instance: {exc}")

        if action_cols[1].button("Stop", key=f"stop-{item['id']}", disabled=stop_disabled):
            try:
                instance_action(item["id"], "stop")
                st.rerun()
            except requests.RequestException as exc:
                st.error(f"Could not stop instance: {exc}")

        if action_cols[2].button("Delete", key=f"delete-{item['id']}", disabled=delete_disabled):
            try:
                delete_instance(item["id"])
                st.session_state["monitor_message"] = f"Instance {item['id']} was deleted."
                st.rerun()
            except requests.RequestException as exc:
                st.error(f"Could not delete instance: {exc}")


with st.sidebar:
    st.header("Client")
    client_name = st.text_input("Client name", value="demo-user")
    include_all = st.checkbox("Show all clients", value=False)

    if st.button("Refresh data"):
        st.rerun()

    st.divider()
    st.header("Create")

    try:
        catalog = load_catalog()
    except requests.RequestException as exc:
        st.error(f"Backend is unavailable: {exc}")
        st.stop()

    instance_type = st.selectbox("Instance type", ["vm", "container"])

    if instance_type == "vm":
        os_name = st.selectbox("OS", list(catalog["vm"].keys()))
        cpu = st.number_input("CPU", min_value=1, max_value=10, value=1, step=1)
        ram_mb = st.number_input("RAM (MB)", min_value=512, max_value=4096, value=1024, step=128)
        disk_gb = st.number_input("Disk (GB)", min_value=5, max_value=50, value=20, step=1)
        lifetime_minutes = st.number_input("Lifetime (minutes)", min_value=1, max_value=1440, value=15, step=1)
        max_cpu_minutes = st.number_input("CPU time limit (minutes)", min_value=0, max_value=1440, value=0, step=1)

        if st.button("Create VM"):
            payload = {
                "client_name": client_name,
                "os_name": os_name,
                "cpu": int(cpu),
                "ram_mb": int(ram_mb),
                "disk_gb": int(disk_gb),
                "lifetime_minutes": int(lifetime_minutes),
                "max_cpu_seconds": int(max_cpu_minutes) * 60,
            }
            try:
                result = create_vm(payload)
                st.session_state["monitor_message"] = f"VM created: {result['id']}"
                st.rerun()
            except requests.RequestException as exc:
                st.error(f"VM creation failed: {exc}")
    else:
        os_name = st.selectbox("Container OS", list(catalog["container"].keys()))
        cpu = st.number_input("CPU", min_value=1, max_value=10, value=1, step=1)
        ram_mb = st.number_input("RAM (MB)", min_value=128, max_value=4096, value=512, step=128)
        lifetime_minutes = st.number_input("Lifetime (minutes)", min_value=1, max_value=1440, value=15, step=1)
        max_cpu_minutes = st.number_input("CPU time limit (minutes)", min_value=0, max_value=1440, value=0, step=1)

        if st.button("Create container"):
            payload = {
                "client_name": client_name,
                "os_name": os_name,
                "cpu": int(cpu),
                "ram_mb": int(ram_mb),
                "lifetime_minutes": int(lifetime_minutes),
                "max_cpu_seconds": int(max_cpu_minutes) * 60,
            }
            try:
                result = create_container(payload)
                st.session_state["monitor_message"] = f"Container created: {result['id']}"
                st.rerun()
            except requests.RequestException as exc:
                st.error(f"Container creation failed: {exc}")


main_col, events_col = st.columns([3, 1])

with main_col:
    if st.session_state["monitor_message"]:
        st.success(st.session_state["monitor_message"])
        st.session_state["monitor_message"] = ""

    try:
        instances = load_instances(client_name=client_name, include_all=include_all)

        if any(item.get("status") == "starting" for item in instances):
            st_autorefresh(interval=3000, key="instances_refresh")

        if not instances:
            st.info("No instances found")
        else:
            for item in instances:
                render_instance_card(item)
    except requests.RequestException as exc:
        st.error(f"Could not load instance list: {exc}")

with events_col:
    st.header("Monitor events")
    if st.button("Run one monitor iteration"):
        try:
            result = run_monitor_once()
            st.session_state["monitor_message"] = f"Monitor iteration completed. Checked {len(result)} instance(s)."
            st.rerun()
        except requests.RequestException as exc:
            st.error(f"Could not run monitor: {exc}")

    try:
        events = load_events(limit=20)
        for event in reversed(events):
            formatted = format_event_line(event)
            if "Manual monitor iteration finished" in formatted:
                continue
            st.code(formatted)
    except requests.RequestException as exc:
        st.error(f"Could not load events: {exc}")
