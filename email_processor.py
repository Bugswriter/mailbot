# email_processor.py (MODIFIED)

import email
import logging
import time
from imap_client import decode_email_header, get_email_body, move_email
from gemini_client import classify_email_with_gemini
from config import SOURCE_INBOX, FOLDER_MAPPING, PROCESS_DELAY_SECONDS, MAX_BODY_CHARS_FOR_GEMINI

logger = logging.getLogger(__name__)

def process_single_email(mail_connection, uid):
    """Processes a single email (by UID): fetches, classifies, and potentially moves it."""
    try:
        # Fetch the email content (RFC822 is the full email raw content)
        # We are using BODY.PEEK[], which *should* prevent marking as \Seen.
        # However, if your server still marks it Seen, we'll immediately unset it below.
        status, msg_data = mail_connection.uid('fetch', uid, '(RFC822 BODY.PEEK[])')
    except Exception as e:
        logger.warning(f"Error fetching content for email UID {uid}: {e}. Skipping.")
        return False # Indicate that processing failed or connection lost

    if status != 'OK':
        logger.error(f"Failed to fetch email content for UID {uid}. Skipping.")
        return False

    # --- ADDED BACK: Explicitly mark as UNSEEN, as a fallback ---
    # This addresses servers that might still mark as \Seen despite BODY.PEEK.
    try:
        mail_connection.uid('store', uid, '-FLAGS', '\\Seen')
        logger.info(f"Explicitly marked email UID {uid} as UNSEEN after fetch.")
    except Exception as e_unseen_fallback:
        logger.warning(f"Failed to explicitly mark email UID {uid} as UNSEEN: {e_unseen_fallback}")

    for response_part in msg_data:
        if isinstance(response_part, tuple):
            try:
                msg_raw = response_part[1] 
                msg = email.message_from_bytes(msg_raw)

                email_subject = decode_email_header(msg.get("subject", ""))
                email_from = decode_email_header(msg.get("from", ""))
                email_body = get_email_body(msg, MAX_BODY_CHARS_FOR_GEMINI)

                logger.info(f"--- Email Details (UID: {uid}) ---")
                logger.info(f"From: {email_from}")
                logger.info(f"Subject: {email_subject}")

                category = classify_email_with_gemini(email_from, email_subject, email_body)
                logger.info(f"Classified as: {category}")

                destination_folder = FOLDER_MAPPING.get(category)
                if not destination_folder:
                    logger.error(f"Unknown category '{category}' or missing in FOLDER_MAPPING. Email UID {uid} will not be moved.")
                    return True 
                
                if destination_folder == SOURCE_INBOX:
                    logger.info(f"Email UID {uid} classified to stay in {SOURCE_INBOX} ({category}).")
                    # It should already be UNSEEN due to the explicit store above.
                else:
                    # Move the email using its UID
                    if not move_email(mail_connection, uid, destination_folder):
                        logger.error(f"Failed to move email UID {uid} to '{destination_folder}'. It might remain in {SOURCE_INBOX}.")
                        return False 
                
                logger.info(f"Waiting for {PROCESS_DELAY_SECONDS} seconds before next email...")
                time.sleep(PROCESS_DELAY_SECONDS)
                return True 

            except Exception as e_inner_proc:
                logger.error(f"Error processing email content for UID {uid}: {e_inner_proc}", exc_info=True)
                # If an error happens *during* processing, ensure it goes back to UNSEEN if possible
                try:
                    mail_connection.uid('store', uid, '-FLAGS', '\\Seen')
                    logger.info(f"Ensured problematic email UID {uid} is marked UNSEEN after processing error.")
                except Exception as e_unseen_fallback:
                    logger.warning(f"Failed to ensure UNSEEN for problematic email UID {uid}: {e_unseen_fallback}")
                return False 

    return False
