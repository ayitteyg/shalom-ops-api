"""
Local development settings.
"""
from .base import *  # noqa: F401,F403

DEBUG = True
SECRET_KEY = "dev-only-insecure-key-do-not-use-in-prod"
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]