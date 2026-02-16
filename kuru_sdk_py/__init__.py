import os
import sys
from loguru import logger

_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logger.remove()
logger.add(sys.stderr, level=_log_level)
