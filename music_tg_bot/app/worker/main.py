import logging
import threading
import time
from pathlib import Path

from redis import Redis
from rq import Worker, Queue, Connection

from app.core.config import settings
from app.core.logging import setup_logging

logger = logging.getLogger("worker")


def cleanup_storage(storage_dir: Path, ttl_hours: int = 24) -> None:
    cutoff = time.time() - ttl_hours * 3600
    for path in storage_dir.glob("*.mp3"):
        if path.stat().st_mtime < cutoff:
            try:
                path.unlink()
            except OSError:
                logger.warning("Не удалось удалить файл: %s", path)


def run_cleanup_loop() -> None:
    while True:
        cleanup_storage(Path(settings.storage_dir))
        time.sleep(3600)


def main() -> None:
    setup_logging()
    redis_conn = Redis.from_url(settings.redis_url)

    thread = threading.Thread(target=run_cleanup_loop, daemon=True)
    thread.start()

    with Connection(redis_conn):
        worker = Worker([Queue("default")])
        worker.work()


if __name__ == "__main__":
    main()
