import argparse
import time

from platform_service import PlatformService


def main() -> None:
    parser = argparse.ArgumentParser(description="Simple monitor for lab 3")
    parser.add_argument("--interval", type=int, default=5, help="monitor interval in seconds")
    args = parser.parse_args()

    service = PlatformService()
    service.events.write("Monitor service started")

    while True:
        service.monitor_once()
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
