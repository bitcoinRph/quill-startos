"""``python -m quill_server``: serve on ``QUILL_PORT``."""

import logging

import uvicorn

from .app import create_app
from .config import Config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    uvicorn.run(create_app(config), host="0.0.0.0", port=config.port, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
