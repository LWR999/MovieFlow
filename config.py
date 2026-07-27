import os

from dotenv import load_dotenv

load_dotenv()

TMDB_API_KEY = os.getenv("TMDB_API_KEY")

TORRENTS_ROOT = "/home/dl/torrents/staging"
INTAKE_FOLDERS = ["1080p", "4K", "Blurays", "Foreign"]

ENCODES = os.environ.get("ENCODES")
ENCODES4K = os.environ.get("ENCODES4K")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mkv_manager.db")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

FLASK_DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
PORT = 5010

# Per-job-type concurrency caps (section 12.4) — conservative defaults,
# tune once real throughput on this box is observed.
MAX_CONCURRENT_PREPROCESS_JOBS = int(os.environ.get("MAX_CONCURRENT_PREPROCESS_JOBS", 2))
MAX_CONCURRENT_PROCESSING_JOBS = int(os.environ.get("MAX_CONCURRENT_PROCESSING_JOBS", 2))
MAX_CONCURRENT_LIBRARY_JOBS = int(os.environ.get("MAX_CONCURRENT_LIBRARY_JOBS", 2))
