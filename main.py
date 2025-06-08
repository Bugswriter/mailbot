import time
import logging
from datetime import datetime, timedelta, timezone 

from config import (
    IMAP_PASSWORD, GEMINI_API_KEY, FOLDER_MAPPING, SOURCE_INBOX,
    CHECK_INTERVAL_SECONDS, LOG_LEVEL, LOG_FILE
)
from imap_client import connect_to_imap, is_imap_connected, get_new_email_uids # MODIFIED: get_new_email_uids
from gemini_client import initialize_gemini 
from email_processor import process_single_email
# NEW: Import UID tracking functions
from processed_uids import is_uid_processed, mark_uid_as_processed 

# --- Configure logging at the very beginning ---
log_level = getattr(logging, LOG_LEVEL.upper(), logging.INFO) 

logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE), 
        logging.StreamHandler()       
    ]
)
logger = logging.getLogger(__name__)

def pre_run_checks():
    """Performs essential checks before starting the main loop."""
    if not IMAP_PASSWORD:
        logger.error("IMAP_PASSWORD environment variable not set. Exiting.")
        return False
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY environment variable not set. Exiting.")
        return False

    # Check if essential destination folders are likely to cause issues
    # These are informative messages, not blockers.
    for category, folder in FOLDER_MAPPING.items():
        if category != "Personal" and folder == SOURCE_INBOX:
             logger.warning(f"WARNING: Category '{category}' is mapped to '{SOURCE_INBOX}'. Emails classified here will NOT be moved out of INBOX.")
        elif folder not in [SOURCE_INBOX, "Accounts", "INBOX.spam", "Junk", "junk e-mail", "[Gmail]/Spam", "[Gmail]/Junk E-mail", "[Gmail]/Promotions"]:
            # More generic check for common folder names if not explicitly mapped
            logger.info(f"INFO: Ensure the IMAP folder '{folder}' for category '{category}' exists on your email server. It's case-sensitive!")
    
    return True

def main():
    logger.info("Email classification script started.")
    if not pre_run_checks():
        return

    initialize_gemini()
    import gemini_client # Re-import the module to ensure we can access its namespace
    
    if not gemini_client.gemini_model: 
        logger.critical("Gemini AI initialization failed. Exiting.")
        return
    
    logger.info(f"Initial wait for {CHECK_INTERVAL_SECONDS} seconds before first email scan.")
    time.sleep(CHECK_INTERVAL_SECONDS)

    mail_connection = None

    while True:
        try:
            # Reconnect IMAP if needed
            if not is_imap_connected(mail_connection):
                logger.info("IMAP connection is not active or lost. Attempting to connect/reconnect...")
                if mail_connection:
                    try:
                        mail_connection.logout()
                    except Exception as e_logout:
                        logger.debug(f"Exception during logout of old connection: {e_logout}")
                        pass # Ignore logout errors during reconnection attempt
                mail_connection = connect_to_imap()

                if not is_imap_connected(mail_connection):
                    logger.error(f"Failed to establish IMAP connection. Retrying in {CHECK_INTERVAL_SECONDS} seconds.")
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

            # Select the inbox in READ-WRITE mode because your bot moves emails.
            status, messages = mail_connection.select(f'"{SOURCE_INBOX}"', readonly=False)
            if status != 'OK':
                logger.error(f"Failed to select inbox {SOURCE_INBOX}: {messages}. Connection might be unstable.")
                if mail_connection:
                    try:
                        mail_connection.logout()
                    except:
                        pass
                mail_connection = None
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            logger.info(f"Checking for UNSEEN emails in '{SOURCE_INBOX}'...")
            
            # MODIFIED: Get UIDs instead of sequence IDs
            unseen_email_uids = get_new_email_uids(mail_connection)
            
            processed_this_cycle = 0
            if unseen_email_uids:
                logger.info(f"Found {len(unseen_email_uids)} UNSEEN email(s) on server (before bot's processed check).")

                for uid in unseen_email_uids:
                    # Check if this UID has already been processed by the bot
                    if is_uid_processed(uid):
                        logger.debug(f"Email UID {uid} already processed by bot. Skipping.")
                        continue

                    logger.info(f"--- Starting processing for UNSEEN email (UID: {uid}) ---")
                    # MODIFIED: Pass UID to process_single_email
                    if process_single_email(mail_connection, uid):
                        mark_uid_as_processed(uid) # Mark as processed ONLY if process_single_email succeeded
                        processed_this_cycle += 1
                        logger.info(f"Email UID {uid} successfully processed and marked in tracking file.")
                    else:
                        logger.warning(f"Failed to process email UID {uid}. Will retry in next cycle if still UNSEEN and not moved.")
                    logger.info(f"--- Finished processing for email (UID: {uid}) ---")

            if processed_this_cycle > 0:
                logger.info(f"Finished processing {processed_this_cycle} new email(s) in this cycle.")
            else:
                logger.info("No new UNSEEN emails to process in this cycle (or all found were already tracked).")

        except Exception as e:
            logger.critical(f"An unexpected critical error occurred in main loop: {e}", exc_info=True)
            # Force reconnection on next loop
            mail_connection = None 
        
        logger.info(f"Waiting for {CHECK_INTERVAL_SECONDS} seconds before next check.")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
