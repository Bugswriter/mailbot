import logging
import os

logger = logging.getLogger(__name__)

UID_FILE = 'processed_uids.txt' # File to store processed UIDs

# In-memory cache for quick lookups
_processed_uids_cache = set()

def load_processed_uids():
    """Loads a set of processed UIDs from the file into the cache."""
    global _processed_uids_cache
    if os.path.exists(UID_FILE):
        try:
            with open(UID_FILE, 'r') as f:
                for line in f:
                    uid = line.strip()
                    if uid:
                        _processed_uids_cache.add(uid)
            logger.info(f"Loaded {len(_processed_uids_cache)} processed UIDs from {UID_FILE}")
        except IOError as e:
            logger.error(f"Error loading processed UIDs from {UID_FILE}: {e}")
    return _processed_uids_cache

def _add_uid_to_file(uid):
    """Internal helper to append a single UID to the file."""
    try:
        with open(UID_FILE, 'a') as f: # 'a' for append mode
            f.write(f"{uid}\n")
        logger.debug(f"Appended UID {uid} to {UID_FILE}.")
    except IOError as e:
        logger.error(f"Error writing UID {uid} to {UID_FILE}: {e}")

def is_uid_processed(uid):
    """Checks if a UID has already been processed by the bot."""
    return uid in _processed_uids_cache

def mark_uid_as_processed(uid):
    """Adds a UID to both the in-memory cache and the file."""
    if uid not in _processed_uids_cache: # Only add if not already present
        _processed_uids_cache.add(uid)
        _add_uid_to_file(uid)
        logger.debug(f"Marked UID {uid} as processed.")
    else:
        logger.debug(f"UID {uid} was already in processed cache, skipping file write.")

# Load UIDs when the module is imported
load_processed_uids()
