import os
from dotenv import load_dotenv
import logging

load_dotenv()

LOG_LEVEL = "DEBUG"   # Set to "DEBUG" for detailed debugging output
LOG_FILE = "mail_manager.log"

# --- IMAP Server Settings ---
IMAP_HOST = 'moose.mxrouting.net'
IMAP_USER = os.getenv('EMAIL_USERNAME')
IMAP_PASSWORD = os.getenv('EMAIL_PASSWORD')
IMAP_PORT = 993
USE_SSL = True

# --- Gemini AI API Key ---
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# --- Email Processing Settings ---
SOURCE_INBOX = "INBOX"
# IMPORTANT: These folder names must EXACTLY match your IMAP folder names (case-sensitive)
FOLDER_MAPPING = {
    "Personal": SOURCE_INBOX,
    "Spam": "INBOX.spam",
    "Accounts": "Accounts",
    "Promotions": "Junk"
}
VALID_CATEGORIES = ["Personal", "Spam", "Accounts", "Promotions"]

# --- Behavior Settings ---
PROCESS_DELAY_SECONDS = 15
CHECK_INTERVAL_SECONDS = 30
MAX_BODY_CHARS_FOR_GEMINI = 2000

# --- Logging Configuration ---
LOG_FILE = 'mail_manager.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
