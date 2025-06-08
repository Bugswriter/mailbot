import imaplib
import email
from email.header import decode_header
import re
from datetime import datetime, timedelta, timezone
import logging
from config import IMAP_HOST, IMAP_PORT, USE_SSL, IMAP_USER, IMAP_PASSWORD, SOURCE_INBOX

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )

def is_imap_connected(connection):
    """Checks if the IMAP connection is alive using NOOP."""
    if not connection:
        return False
    try:
        if not hasattr(connection, 'socket') or connection.socket() is None:
            logger.debug("is_imap_connected: No active socket found for the connection object.")
            return False
        status, _ = connection.noop()
        return status == 'OK'
    except (imaplib.IMAP4.abort, imaplib.IMAP4.error, BrokenPipeError, OSError) as e:
        logger.warning(f"is_imap_connected: noop check failed with {type(e).__name__}: {e}")
        return False
    except AttributeError as e:
        logger.warning(f"is_imap_connected: AttributeError on connection object: {e}")
        return False
    except Exception as e:
        logger.error(f"is_imap_connected: Unexpected error during NOOP check: {e}", exc_info=True)
        return False

def connect_to_imap():
    """Connects to the IMAP server and logs in."""
    try:
        if USE_SSL:
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        else:
            mail = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USER, IMAP_PASSWORD)
        logger.info(f"Successfully connected to IMAP server: {IMAP_HOST}")
        return mail
    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP connection error: {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during IMAP connection: {e}")
        return None

def decode_email_header(header):
    """Decodes email headers to a readable string."""
    if not header:
        return ""
    decoded_parts = decode_header(header)
    header_parts = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            try:
                header_parts.append(part.decode(charset or 'utf-8', 'ignore'))
            except (LookupError, UnicodeDecodeError):
                try:
                    header_parts.append(part.decode('latin-1', 'ignore'))
                except (LookupError, UnicodeDecodeError):
                    try:
                        header_parts.append(part.decode('cp1252', 'ignore'))
                    except (LookupError, UnicodeDecodeError):
                        header_parts.append(part.decode('utf-8', 'ignore')) # Final fallback
        else:
            header_parts.append(part)
    return "".join(header_parts)

def get_email_body(msg, max_chars=None):
    """Extracts the plain text body from an email message, optionally truncating it."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == 'text/plain' and 'attachment' not in content_disposition:
                try:
                    charset = part.get_content_charset() or 'utf-8'
                    body = part.get_payload(decode=True).decode(charset, 'ignore')
                    break
                except Exception as e:
                    logger.warning(f"Could not decode text/plain part: {e}")
                    continue
    else:
        if msg.get_content_type() == 'text/plain':
            try:
                charset = msg.get_content_charset() or 'utf-8'
                body = msg.get_payload(decode=True).decode(charset, 'ignore')
            except Exception as e:
                logger.warning(f"Could not decode non-multipart text/plain body: {e}")
    
    body = body.strip()
    if max_chars and len(body) > max_chars:
        logger.debug(f"Email body truncated from {len(body)} to {max_chars} chars.")
        return body[:max_chars]
    return body

# MODIFIED: This function now returns UIDs, not sequence IDs.
def get_new_email_uids(mail_connection):
    """
    Fetches UNSEEN email UIDs from the inbox.
    We are no longer using date-based filtering here because external tracking handles
    re-processing. We just need all currently UNSEEN emails.
    Args:
        mail_connection: The IMAP4 connection object.
    Returns:
        list: A list of email UIDs (strings).
    """
    logger.debug(f"IMAP search for UNSEEN emails in '{SOURCE_INBOX}' using UID search.")
    # Use UID SEARCH to get UIDs directly
    typ, data = mail_connection.uid('search', None, 'UNSEEN')
    if typ != 'OK':
        logger.error(f"Error searching for UNSEEN email UIDs: {data}. Connection might be unstable.")
        return []

    # data[0] contains space-separated UIDs as bytes (e.g., b'1 2 3')
    unseen_uids_bytes = data[0].split()
    unseen_uids_str = [uid_b.decode() for uid_b in unseen_uids_bytes]
    
    logger.debug(f"Found {len(unseen_uids_str)} UNSEEN email UIDs.")
    return unseen_uids_str

# MODIFIED: This function now accepts UID instead of email_id for move operation.
# It also does not try to mark UNSEEN in the destination as this is not always supported
# and the source email is deleted.
def move_email(mail_connection, uid, destination_folder_name):
    """Moves an email (by UID) to the specified destination folder."""
    if not mail_connection:
        logger.error("move_email called with a None mail_connection object.")
        return False

    try:
        # Copy the email to the destination folder using UID COPY
        # The 'UID' argument is important here.
        apply_label_result = mail_connection.uid('copy', uid, destination_folder_name)
        if apply_label_result[0] == 'OK':
            logger.info(f"Successfully copied email UID {uid} to folder '{destination_folder_name}'.")
            
            # Mark the original email as deleted from SOURCE_INBOX using UID STORE
            delete_result = mail_connection.uid('store', uid, '+FLAGS', '\\Deleted')
            if delete_result[0] == 'OK':
                logger.info(f"Successfully marked email UID {uid} as deleted from {SOURCE_INBOX}.")
                # Expunge deleted emails (only affects the currently selected folder)
                expunge_result = mail_connection.expunge()
                if expunge_result[0] == 'OK':
                    logger.info(f"Successfully expunged deleted emails from {SOURCE_INBOX}.")
                    return True
                else:
                    logger.error(f"Failed to expunge emails from {SOURCE_INBOX}: {expunge_result}")
                    return False
            else:
                logger.error(f"Failed to mark email UID {uid} as deleted: {delete_result}")
                return False
        else:
            logger.error(f"Failed to copy email UID {uid} to '{destination_folder_name}': {apply_label_result}")
            if "TRYCREATE" in str(apply_label_result[1]).upper() or "NONEXISTENT" in str(apply_label_result[1]).upper():
                logger.error(f"The folder '{destination_folder_name}' likely does not exist on the server. Please create it.")
            return False
    except (imaplib.IMAP4.abort, imaplib.IMAP4.error) as e:
        logger.error(f"IMAP operation error in move_email for UID {uid}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error while moving email UID {uid}: {e}", exc_info=True)
    return False

# --- Standalone Execution Block for Testing ---
if __name__ == "__main__":
    logger.info("--- Starting standalone IMAP client test ---")
    
    mail_connection = None
    try:
        mail_connection = connect_to_imap()
        if not mail_connection:
            logger.error("Failed to connect to IMAP server for testing. Exiting.")
            exit(1)

        # Select inbox in read-write mode to allow expunge in test
        status, messages = mail_connection.select(f'"{SOURCE_INBOX}"', readonly=False)
        if status != 'OK':
            logger.error(f"Failed to select inbox {SOURCE_INBOX} for testing: {messages}. Exiting.")
            exit(1)

        logger.info(f"Attempting to fetch UNSEEN email UIDs from '{SOURCE_INBOX}'.")
        
        found_email_uids = get_new_email_uids(mail_connection)

        if found_email_uids:
            logger.info(f"Successfully found {len(found_email_uids)} UNSEEN email(s).")
            for i, uid in enumerate(found_email_uids):
                logger.info(f"  {i+1}. Email UID: {uid}")
                # Optionally, fetch subject/from to see more details using UID FETCH BODY.PEEK
                try:
                    status, msg_data = mail_connection.uid('fetch', uid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
                    if status == 'OK' and msg_data and isinstance(msg_data[0], tuple):
                        msg = email.message_from_bytes(msg_data[0][1])
                        subject = decode_email_header(msg.get("subject", "No Subject"))
                        sender = decode_email_header(msg.get("from", "No Sender"))
                        date_header = decode_email_header(msg.get("date", "No Date"))
                        logger.info(f"    From: {sender}")
                        logger.info(f"    Subject: {subject}")
                        logger.info(f"    Date: {date_header}")
                        # No need to explicitly mark UNSEEN here; BODY.PEEK ensures it.
                    else:
                        logger.warning(f"Failed to fetch header for email UID {uid}.")
                except Exception as e:
                    logger.error(f"Error fetching/decoding headers for {uid}: {e}", exc_info=True)
        else:
            logger.info(f"No UNSEEN emails found in '{SOURCE_INBOX}'.")

    except Exception as e:
        logger.critical(f"An unexpected error occurred during the standalone test: {e}", exc_info=True)
    finally:
        if mail_connection:
            try:
                mail_connection.close()
                mail_connection.logout()
                logger.info("Logged out from IMAP server.")
            except Exception as e:
                logger.error(f"Error during final logout in test: {e}")
    logger.info("--- Standalone IMAP client test finished ---")
