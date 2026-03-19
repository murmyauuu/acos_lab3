# Лабораторная работа 3 — сервис аренды виртуальных машин и контейнеров

## Авторы

- Данилин Егор
- Малышев Артём
- Ошкин Артём

## Описание проекта

Проект представляет собой сервис аренды вычислительных ресурсов. Пользователь через web-интерфейс может создать:

- виртуальную машину на базе **QEMU**;
- контейнер на базе **Docker**.

Для каждого созданного экземпляра выдаются:

- отдельный SSH-порт;
- логин и пароль;
- параметры ресурсов (CPU, RAM, для VM также диск);
- ограничения по времени жизни и по CPU time.

Также в проекте есть отдельный **monitor-сервис**, который периодически проверяет состояние экземпляров и автоматически останавливает их при достижении лимитов.

## Что реализовано

- web-интерфейс на **Streamlit**;
- backend API на **FastAPI**;
- создание VM через **QEMU** и **cloud-init**;
- создание контейнеров через **Docker**;
- доступ к VM и контейнерам по **SSH**;
- хранение состояния инстансов в локальном `JSON`;
- журнал событий monitor-сервиса;
- автоматическая остановка по:
  - времени аренды (`lifetime_minutes`);
  - лимиту процессорного времени (`max_cpu_seconds`).

## Структура проекта

- `frontend.py` — web-интерфейс на Streamlit;
- `backend.py` — REST API на FastAPI;
- `platform_service.py` — основная бизнес-логика;
- `monitor.py` — отдельный процесс мониторинга;
- `dockerfiles/` — Dockerfile для контейнерных образов;
- `data/` — локальные данные проекта:
  - `base_images/` — кэш cloud image;
  - `vms/` — диски и cloud-init файлы отдельных виртуальных машин;
  - `state/` — `instances.json` и журнал событий.

## Архитектура

Проект состоит из трех логических частей:

1. **Frontend** отправляет запросы на backend и отображает список инстансов, их параметры, SSH-команды и события monitor.
2. **Backend** принимает запросы на создание, запуск, остановку и удаление инстансов.
3. **PlatformService** управляет двумя типами сущностей:
   - `VMManager` — виртуальные машины на QEMU;
   - `ContainerManager` — контейнеры на Docker.
4. **Monitor** периодически вызывает проверку всех инстансов и завершает их по условиям аренды.

## Требования к окружению

Нужно установить:

- Python **3.11+**;
- Docker и запущенный Docker daemon;
- `qemu-system-x86_64`;
- `qemu-img`;
- `genisoimage`;
- SSH-клиент.

### Для Linux / WSL

Все команды ниже рассчитаны на Linux или WSL. Если проект запускается на Windows, рекомендуется использовать **WSL2 с Ubuntu на борту**.

## Установка зависимостей

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Запуск проекта

### 1. Запуск backend

```bash
uvicorn backend:app --host 127.0.0.1 --port 8000
```

### 2. Запуск monitor

В отдельном терминале:

```bash
python monitor.py --interval 5
```

### 3. Запуск frontend

В отдельном терминале:

```bash
streamlit run frontend.py
```

После запуска интерфейс Streamlit откроется в браузере.

## Основные возможности API

- `GET /health` — проверка доступности backend;
- `GET /catalog` — список доступных ОС для VM и контейнеров;
- `POST /instances/vm` — создать виртуальную машину;
- `POST /instances/container` — создать контейнер;
- `GET /instances` — получить список инстансов;
- `GET /instances/{instance_id}` — получить информацию по одному инстансу;
- `POST /instances/{instance_id}/action?action=start|stop` — запуск / остановка;
- `DELETE /instances/{instance_id}` — удаление;
- `GET /monitor/events` — журнал monitor;
- `POST /monitor/run-once` — однократный запуск проверки monitor.

## Пример подключения по SSH

```bash
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null tenant@localhost -p <порт>
```


