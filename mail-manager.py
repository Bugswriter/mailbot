import imaplib
import email
from email.header import decode_header
from dotenv import load_dotenv
import google.generativeai as genai
import time
import logging
import os
import re

load_dotenv()

# --- CONFIGURATION ---
# IMAP Server Settings (Update with your details)
IMAP_HOST = 'moose.mxrouting.net'  # e.g., 'imap.gmail.com'
IMAP_USER = os.getenv('EMAIL_USERNAME')
IMAP_PASSWORD = os.getenv('EMAIL_PASSWORD') # Recommended: Store password in environment variable
IMAP_PORT = 993  # Default for IMAP SSL
USE_SSL = True

# Gemini AI API Key (Update with your key)
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') # Recommended: Store API key in environment variable

# Email Processing Settings
SOURCE_INBOX = "Inbox"  # Where new emails arrive
# IMPORTANT: These folder names must EXACTLY match your IMAP folder names (case-sensitive)
FOLDER_MAPPING = {
    "Personal": SOURCE_INBOX, # Personal emails stay in the Inbox (or move if SOURCE_INBOX is not "INBOX")
    "Spam": "Inbox.spam",           # Your existing 'spam' folder (e.g., Gmail's is often '[Gmail]/Spam')
    "Accounts": "Accounts",   # This is a NEW folder you need to create
    "Promotions": "Junk"      # Your existing 'Junk' folder (some use 'Junk E-mail')
}
VALID_CATEGORIES = ["Personal", "Spam", "Accounts", "Promotions"]

# Behavior Settings
PROCESS_DELAY_SECONDS = 15  # Delay between processing each email (to be gentle on Gemini API)
CHECK_INTERVAL_SECONDS = 300 # How often to check for new emails when inbox is empty (5 minutes)
MAX_BODY_CHARS_FOR_GEMINI = 2000 # Max characters of body to send to Gemini (adjust as needed)

# Logging Configuration
LOG_FILE = 'mail_manager.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

# --- GEMINI AI SETUP ---
try:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable not set.")
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-1.5-flash-latest') # Or your preferred model
except Exception as e:
    logging.error(f"Failed to configure Gemini AI: {e}")
    exit()

# --- HELPER FUNCTIONS ---
def is_imap_connected(connection):
    """Checks if the IMAP connection is alive using NOOP."""
    if not connection: # Handles case where connection object is None
        return False
    try:
        # Check if the 'socket' attribute exists and the socket it returns is not None.
        # This helps avoid errors if the connection object is not fully initialized or already closed.
        if not hasattr(connection, 'socket') or connection.socket() is None:
            logging.debug("is_imap_connected: No active socket found for the connection object.")
            return False

        status, _ = connection.noop()
        return status == 'OK'
    except (imaplib.IMAP4.abort, imaplib.IMAP4.error, BrokenPipeError, OSError) as e:
        # These exceptions typically indicate a dead or problematic connection
        logging.warning(f"is_imap_connected: noop check failed with {type(e).__name__}: {e}")
        return False
    except AttributeError as e:
        # Catching AttributeError if 'noop' or other methods are missing,
        # which would indicate 'connection' is not a valid IMAP4 object.
        logging.warning(f"is_imap_connected: AttributeError on connection object: {e}")
        return False
    except Exception as e: # Catch any other unexpected exceptions
        logging.error(f"is_imap_connected: Unexpected error during NOOP check: {e}", exc_info=True)
        return False

# ... (other helper functions like connect_to_imap, decode_email_header, etc.) ...

def clean_filename(name):
    """Remove or replace characters that are invalid in IMAP folder names."""
    # Replace common problematic characters with underscores or remove them
    # This is a basic version; IMAP folder name restrictions can vary.
    # Check your server's specific restrictions if you encounter issues.
    name = name.replace('/', '_') # Forward slashes are common delimiters
    name = name.replace('\\', '_')
    name = re.sub(r'[^\x00-\x7F]+', '', name) # Remove non-ASCII characters
    # Add more replacements if needed, e.g., for brackets, parentheses etc.
    # For example, some servers might not like leading/trailing spaces or special chars like '*'
    return name

def connect_to_imap():
    """Connects to the IMAP server and logs in."""
    try:
        if USE_SSL:
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        else:
            mail = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USER, IMAP_PASSWORD)
        logging.info(f"Successfully connected to IMAP server: {IMAP_HOST}")
        return mail
    except imaplib.IMAP4.error as e:
        logging.error(f"IMAP connection error: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred during IMAP connection: {e}")
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
            except LookupError: # Unknown encoding
                header_parts.append(part.decode('utf-8', 'ignore')) # Fallback
        else:
            header_parts.append(part)
    return "".join(header_parts)

def get_email_body(msg):
    """Extracts the plain text body from an email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == 'text/plain' and 'attachment' not in content_disposition:
                try:
                    charset = part.get_content_charset() or 'utf-8'
                    body = part.get_payload(decode=True).decode(charset, 'ignore')
                    break # Take the first plain text part
                except Exception as e:
                    logging.warning(f"Could not decode text/plain part: {e}")
                    continue
    else: # Not multipart, try to get the plain text body directly
        if msg.get_content_type() == 'text/plain':
            try:
                charset = msg.get_content_charset() or 'utf-8'
                body = msg.get_payload(decode=True).decode(charset, 'ignore')
            except Exception as e:
                logging.warning(f"Could not decode non-multipart text/plain body: {e}")
    return body.strip()


def classify_email_with_gemini(sender, subject, body):
    """Classifies email content using Gemini AI."""
    if not subject and not body:
        logging.warning("Email has no subject or body, classifying as Promotions by default.")
        return "Promotions"

    # Truncate body if it's too long
    truncated_body = body[:MAX_BODY_CHARS_FOR_GEMINI]
    if len(body) > MAX_BODY_CHARS_FOR_GEMINI:
        logging.info(f"Email body truncated to {MAX_BODY_CHARS_FOR_GEMINI} chars for Gemini.")

    prompt = f"""Analyze the following email's sender, subject, and body, then assign it one of the following four categories. Your response MUST be only one of these exact words: "Personal", "Spam", "Accounts", or "Promotions".

Category Definitions:
1. Personal: Emails written by a human directly to me. These are typically from friends, family, or colleagues for direct conversation, and are not automated or mass-sent.
2. Spam: Unsolicited, unwanted commercial emails, scams, phishing attempts, suspicious content, or anything clearly undesired.
3. Accounts: Transactional emails from services or businesses where I have an an active account. This includes bank statements, transaction alerts, invoices, order confirmations, shipping updates, password resets, OTPs, or critical subscription notifications.
4. Promotions: Marketing emails, newsletters, advertisements, sales announcements, or general non-critical updates from businesses or organizations that I might have subscribed to but are not essential transactional information.

If you are unsure or if the content is unclear, categorize it as "Promotions" unless it explicitly looks like spam.

---
Email to classify:
Sender: {sender}
Subject: {subject}
Body:
{truncated_body}
---
Category:"""

    try:
        logging.debug(f"Sending prompt to Gemini: \n{prompt[:300]}...") # Log a snippet
        response = gemini_model.generate_content(prompt)
        category = response.text.strip()
        if category in VALID_CATEGORIES:
            logging.info(f"Gemini classified email as: {category}")
            return category
        else:
            logging.warning(f"Gemini returned an invalid category: '{category}'. Defaulting to 'Promotions'. Response: {response.text}")
            return "Promotions"
    except Exception as e:
        logging.error(f"Error calling Gemini API: {e}. Defaulting to 'Promotions'.")
        return "Promotions"

def move_email(mail_connection, email_id, destination_folder_name):
    """Moves an email to the specified destination folder."""
    # REMOVED: if not mail_connection or not mail_connection.socket().is_connected():
    # The main loop is now primarily responsible for ensuring mail_connection is active.

    if not mail_connection: # Basic check in case it's explicitly None
        logging.error("move_email called with a None mail_connection object.")
        return False

    try:
        # Ensure the destination folder name is correctly encoded for IMAP (usually UTF-7 variant for non-ASCII)
        cleaned_folder_name = destination_folder_name # Using it directly as per FOLDER_MAPPING

        # Copy the email to the destination folder
        apply_label_result = mail_connection.copy(email_id, cleaned_folder_name)
        if apply_label_result[0] == 'OK':
            logging.info(f"Successfully copied email ID {email_id.decode()} to folder '{cleaned_folder_name}'.")
            # Mark the original email as deleted
            delete_result = mail_connection.store(email_id, '+FLAGS', '\\Deleted')
            if delete_result[0] == 'OK':
                logging.info(f"Successfully marked email ID {email_id.decode()} as deleted from {SOURCE_INBOX}.")
                # Permanently remove deleted emails from the source inbox
                expunge_result = mail_connection.expunge()
                if expunge_result[0] == 'OK':
                    logging.info(f"Successfully expunged deleted emails from {SOURCE_INBOX}.")
                    return True
                else:
                    logging.error(f"Failed to expunge emails from {SOURCE_INBOX}: {expunge_result}")
                    return False
            else:
                logging.error(f"Failed to mark email ID {email_id.decode()} as deleted: {delete_result}")
                return False
        else:
            logging.error(f"Failed to copy email ID {email_id.decode()} to '{cleaned_folder_name}': {apply_label_result}")
            if "TRYCREATE" in str(apply_label_result[1]).upper() or "NONEXISTENT" in str(apply_label_result[1]).upper():
                 logging.error(f"The folder '{cleaned_folder_name}' likely does not exist on the server. Please create it.")
            return False
    except (imaplib.IMAP4.abort, imaplib.IMAP4.error) as e: # Catch specific IMAP connection/operation errors
        logging.error(f"IMAP operation error in move_email for ID {email_id.decode() if isinstance(email_id, bytes) else email_id}: {e}")
        # This error can indicate a dropped connection. The main loop should handle reconnection.
        return False
    except Exception as e:
        logging.error(f"Unexpected error while moving email ID {email_id.decode() if isinstance(email_id, bytes) else email_id}: {e}")
        return False

# --- MAIN PROCESSING LOOP ---
# --- MAIN PROCESSING LOOP ---
def main():
    logging.info("Email classification script started.")
    if not IMAP_PASSWORD:
        logging.error("IMAP_PASSWORD environment variable not set. Exiting.")
        return
    if not GEMINI_API_KEY:
        logging.error("GEMINI_API_KEY environment variable not set. Exiting.")
        return

    mail_connection = None

    while True:
        try:
            # Check connection status using NOOP at the beginning of each major cycle
            if not is_imap_connected(mail_connection):
                logging.info("IMAP connection is not active or lost. Attempting to connect/reconnect...")
                if mail_connection: # If there was an old connection object, try to logout
                    try:
                        mail_connection.logout()
                    except Exception as e_logout: # nosec
                        logging.debug(f"Exception during logout of old connection: {e_logout}")
                        pass # Ignore errors during logout of a potentially dead connection
                mail_connection = connect_to_imap()

                # After attempting to connect, check again
                if not is_imap_connected(mail_connection):
                    logging.error(f"Failed to establish IMAP connection. Retrying in {CHECK_INTERVAL_SECONDS} seconds.")
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue # Skip to the next iteration of the while loop

            # Select the source inbox
            # It's good practice to select mailbox in each iteration if connection might have been re-established.
            status, messages = mail_connection.select(f'"{SOURCE_INBOX}"', readonly=False)
            if status != 'OK':
                logging.error(f"Failed to select inbox {SOURCE_INBOX}: {messages}. Connection might be unstable.")
                # Consider the connection problematic, force re-evaluation in next loop iteration
                if mail_connection:
                    try:
                        mail_connection.logout()
                    except: # nosec
                        pass
                mail_connection = None # Force reconnect in the next iteration
                time.sleep(CHECK_INTERVAL_SECONDS) # Wait before retrying
                continue

            # Search for emails (using 'ALL' and checking '\\Seen' flag locally)
            typ, data = mail_connection.search(None, 'ALL')
            if typ != 'OK':
                logging.error("Error searching for emails. Connection might be unstable.")
                # Consider connection problematic
                if mail_connection:
                    try:
                        mail_connection.logout()
                    except: # nosec
                        pass
                mail_connection = None # Force reconnect
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            email_ids = data[0].split()
            if not email_ids:
                logging.info(f"No emails found in {SOURCE_INBOX} during this check (or all are processed). Waiting for {CHECK_INTERVAL_SECONDS} seconds.")
                # No need to close mailbox if we are just going to sleep and re-select
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            logging.info(f"Found {len(email_ids)} email items to check in {SOURCE_INBOX}.")
            processed_one_email_in_batch = False

            for email_id in reversed(email_ids): # Process oldest first if desired
                # Fetch email flags to check if it's unread ('\Seen')
                # This fetch itself can fail if connection dropped since search
                try:
                    status, flag_data = mail_connection.fetch(email_id, '(FLAGS)')
                except (imaplib.IMAP4.abort, imaplib.IMAP4.error) as e_fetch_flags:
                    logging.warning(f"IMAP error fetching flags for email ID {email_id.decode() if isinstance(email_id, bytes) else email_id}: {e_fetch_flags}. Assuming connection issue.")
                    # Break from this inner loop to re-evaluate connection at the top of the while loop
                    if mail_connection:
                        try: mail_connection.logout()
                        except: pass # nosec
                    mail_connection = None
                    break # break from for loop

                if status != 'OK':
                    logging.error(f"Failed to fetch flags for email ID {email_id.decode() if isinstance(email_id, bytes) else email_id}. Skipping.")
                    continue

                flags = flag_data[0].decode()
                if '\\Seen' in flags:
                    continue # Skip already read emails

                logging.info(f"Processing unread email ID: {email_id.decode()}")
                processed_one_email_in_batch = True

                # Fetch the email RFC822 data
                try:
                    status, msg_data = mail_connection.fetch(email_id, '(RFC822)')
                except (imaplib.IMAP4.abort, imaplib.IMAP4.error) as e_fetch_rfc822:
                    logging.warning(f"IMAP error fetching content for email ID {email_id.decode() if isinstance(email_id, bytes) else email_id}: {e_fetch_rfc822}. Assuming connection issue.")
                    if mail_connection:
                        try: mail_connection.logout()
                        except: pass # nosec
                    mail_connection = None
                    break # break from for loop

                if status != 'OK':
                    logging.error(f"Failed to fetch email content for ID {email_id.decode()}. Skipping.")
                    continue

                # ... (rest of the email processing: parsing, classifying, moving) ...
                # Ensure that the 'move_email' call is within this try block or its own
                # that can handle exceptions and potentially signal a reconnect.
                # The existing structure for processing a single email seems fine.
                # Original code for processing a single email:
                for response_part in msg_data: # This loop structure is fine
                    if isinstance(response_part, tuple):
                        try:
                            msg_raw = response_part[1]
                            msg = email.message_from_bytes(msg_raw)

                            email_subject = decode_email_header(msg["subject"])
                            email_from = decode_email_header(msg["from"])
                            email_body = get_email_body(msg)

                            logging.info(f"--- Email Details ---")
                            logging.info(f"From: {email_from}")
                            logging.info(f"Subject: {email_subject}")

                            category = classify_email_with_gemini(email_from, email_subject, email_body)
                            logging.info(f"Classified as: {category}")

                            destination_folder = FOLDER_MAPPING.get(category)
                            if not destination_folder:
                                logging.error(f"Unknown category '{category}' or missing in FOLDER_MAPPING. Email ID {email_id.decode()} will not be moved.")
                                continue # Next email_id

                            if destination_folder == SOURCE_INBOX: # Covers "Personal" if SOURCE_INBOX is "INBOX"
                                logging.info(f"Email ID {email_id.decode()} classified to stay in {SOURCE_INBOX} ({category}). Marking as read.")
                                mail_connection.store(email_id, '+FLAGS', '\\Seen')
                            else:
                                if move_email(mail_connection, email_id, destination_folder):
                                    logging.info(f"Successfully moved email ID {email_id.decode()} to '{destination_folder}'.")
                                else:
                                    logging.error(f"Failed to move email ID {email_id.decode()} to '{destination_folder}'. It might remain in {SOURCE_INBOX}.")
                                    # If move_email returned False due to IMAP error, connection might be dead.
                                    # The main loop's exception handler or the next is_imap_connected check will catch this.
                                    # To be safer, if move_email fails, we could aggressively check connection:
                                    if not is_imap_connected(mail_connection):
                                        logging.warning("Connection lost after failed move. Forcing reconnect.")
                                        if mail_connection:
                                            try: mail_connection.logout()
                                            except: pass # nosec
                                        mail_connection = None
                                        break # Break from for-loop to force reconnect in while-loop

                            logging.info(f"Waiting for {PROCESS_DELAY_SECONDS} seconds before next email...")
                            time.sleep(PROCESS_DELAY_SECONDS)
                            break # Processed this email_id from msg_data, move to next email_id from outer loop
                        except Exception as e_inner_proc: # This catches errors within processing a single email
                            logging.error(f"Error processing email content for ID {email_id.decode()}: {e_inner_proc}", exc_info=True)
                            try:
                                # Try to mark as read to avoid reprocessing loop for this specific problematic email
                                mail_connection.store(email_id, '+FLAGS', '\\Seen')
                                logging.info(f"Marked problematic email ID {email_id.decode()} as read to prevent reprocessing loop.")
                            except Exception as e_mark_read:
                                logging.error(f"Failed to mark problematic email ID {email_id.decode()} as read: {e_mark_read}")
                                # If marking as read fails, connection is likely very bad
                                if mail_connection:
                                    try: mail_connection.logout()
                                    except: pass # nosec
                                mail_connection = None # Force reconnect
                                break # Break from for-loop
                if mail_connection is None: # If a break occurred that nulled mail_connection
                    break # Break from the outer for loop (email_ids) to re-evaluate connection

            if mail_connection is None: # If connection was lost and outer for loop was broken
                logging.info("Connection lost during email processing batch. Will attempt reconnect.")
                time.sleep(CHECK_INTERVAL_SECONDS) # Wait before trying to reconnect
                continue


            if not processed_one_email_in_batch:
                 logging.info(f"No unread emails processed in this pass from {SOURCE_INBOX}. Waiting for {CHECK_INTERVAL_SECONDS} seconds.")
                 # No need to mail_connection.close() here, select() will be called again.
                 time.sleep(CHECK_INTERVAL_SECONDS)


        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, ConnectionResetError) as e: # Main loop's IMAP error catcher
            logging.error(f"Main loop IMAP connection issue: {e}. Attempting to re-establish connection.")
            if mail_connection:
                try:
                    mail_connection.logout() # Attempt to clean up
                except: # nosec
                    pass
            mail_connection = None # Ensure re-connection attempt in the next iteration
            time.sleep(60) # Wait a bit longer after these types of errors before retrying
        except KeyboardInterrupt:
            logging.info("Script interrupted by user. Shutting down...")
            break
        except Exception as e: # Catch-all for any other unexpected errors in the main loop
            logging.critical(f"An unhandled critical error occurred in the main loop: {e}", exc_info=True)
            if mail_connection:
                try:
                    mail_connection.logout()
                except: # nosec
                    pass
            mail_connection = None
            logging.info(f"Restarting loop after 60 seconds due to critical error.")
            time.sleep(60)

    # ... (rest of the script, final logout) ...
    if mail_connection and is_imap_connected(mail_connection): # Check before final logout
        try:
            logging.info("Logging out from IMAP server.")
            mail_connection.close() # Close the selected mailbox
            mail_connection.logout()
        except Exception as e:
            logging.error(f"Error during final logout: {e}")
    logging.info("Email classification script finished.")

if __name__ == '__main__':
    # --- PRE-RUN CHECKS ---
    if not IMAP_PASSWORD:
        print("ERROR: IMAP_PASSWORD environment variable not set. Please set it before running.")
        print("Example: export EMAIL_PASSWORD='your_actual_password'")
        exit(1)
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY environment variable not set. Please set it before running.")
        print("Example: export GEMINI_API_KEY='your_gemini_api_key'")
        exit(1)

    # Check if essential destination folders are likely to cause issues
    if FOLDER_MAPPING["Accounts"] == "Accounts": # Assuming this is the new one
        print(f"INFO: Ensure the IMAP folder '{FOLDER_MAPPING['Accounts']}' exists on your email server.")
    if FOLDER_MAPPING["Spam"].lower() not in ["spam", "[gmail]/spam"]: # Common spam folder names
        print(f"INFO: Ensure the IMAP folder '{FOLDER_MAPPING['Spam']}' exists and is correctly named.")
    if FOLDER_MAPPING["Promotions"].lower() not in ["junk", "junk e-mail", "[gmail]/junk"]:
         print(f"INFO: Ensure the IMAP folder '{FOLDER_MAPPING['Promotions']}' exists and is correctly named.")

    main()
