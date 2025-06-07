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
SOURCE_INBOX = "INBOX"  # Where new emails arrive
# IMPORTANT: These folder names must EXACTLY match your IMAP folder names (case-sensitive)
FOLDER_MAPPING = {
    "Personal": SOURCE_INBOX, # Personal emails stay in the Inbox (or move if SOURCE_INBOX is not "INBOX")
    "Spam": "INBOX.spam",       # Your existing 'spam' folder (e.g., Gmail's is often '[Gmail]/Spam')
    "Accounts": "Accounts",    # This is a NEW folder you need to create
    "Promotions": "Junk"       # Your existing 'Junk' folder (some use 'Junk E-mail')
}
VALID_CATEGORIES = ["Personal", "Spam", "Accounts", "Promotions"]

# Behavior Settings
PROCESS_DELAY_SECONDS = 60   # Delay between processing each email (to be gentle on Gemini API)
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
    if not mail_connection:
        logging.error("move_email called with a None mail_connection object.")
        return False

    try:
        cleaned_folder_name = destination_folder_name

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
    except (imaplib.IMAP4.abort, imaplib.IMAP4.error) as e:
        logging.error(f"IMAP operation error in move_email for ID {email_id.decode() if isinstance(email_id, bytes) else email_id}: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error while moving email ID {email_id.decode() if isinstance(email_id, bytes) else email_id}: {e}")
        return False


# --- MAIN PROCESSING LOOP (REVISED) ---
def main():
    logging.info("Email classification script started.")
    if not IMAP_PASSWORD or not GEMINI_API_KEY:
        logging.error("IMAP_PASSWORD or GEMINI_API_KEY environment variable not set. Exiting.")
        return

    mail_connection = None

    while True:
        try:
            if not is_imap_connected(mail_connection):
                logging.info("IMAP connection is not active. Attempting to connect...")
                if mail_connection:
                    try: mail_connection.logout()
                    except Exception: pass
                mail_connection = connect_to_imap()

                if not is_imap_connected(mail_connection):
                    logging.error(f"Failed to connect. Retrying in {CHECK_INTERVAL_SECONDS} seconds.")
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

            status, _ = mail_connection.select(f'"{SOURCE_INBOX}"', readonly=False)
            if status != 'OK':
                logging.error(f"Failed to select inbox {SOURCE_INBOX}. Resetting connection.")
                if mail_connection:
                    try: mail_connection.logout()
                    except Exception: pass
                mail_connection = None
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            ### MODIFICATION ###
            # Search for only UNSEEN (unread) emails. This is more efficient
            # and is the first step to not touching already-read emails.
            typ, data = mail_connection.search(None, 'UNSEEN')
            if typ != 'OK':
                logging.error("Error searching for unread emails. Resetting connection.")
                if mail_connection:
                    try: mail_connection.logout()
                    except Exception: pass
                mail_connection = None
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            email_ids = data[0].split()
            if not email_ids:
                logging.info(f"No unread emails found in {SOURCE_INBOX}. Waiting for {CHECK_INTERVAL_SECONDS} seconds.")
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            logging.info(f"Found {len(email_ids)} unread email(s) to process in {SOURCE_INBOX}.")

            for email_id in reversed(email_ids):
                if not is_imap_connected(mail_connection):
                    logging.warning("Connection lost during batch processing. Breaking to reconnect.")
                    break # Break from the for-loop to trigger reconnect at the start of the while-loop

                logging.info(f"Processing unread email ID: {email_id.decode()}")

                # Fetching the email will mark it as read ('\Seen') by default.
                # We will manually change this status back to unread for specific categories.
                status, msg_data = mail_connection.fetch(email_id, '(RFC822)')
                if status != 'OK':
                    logging.error(f"Failed to fetch email content for ID {email_id.decode()}. Skipping.")
                    continue

                for response_part in msg_data:
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
                            destination_folder = FOLDER_MAPPING.get(category)

                            if not destination_folder:
                                logging.error(f"Unknown category '{category}'. Email ID {email_id.decode()} will not be moved.")
                                continue

                            ### MODIFICATION ###
                            # Based on the category, decide the read/unread status.
                            if category == "Personal":
                                # This email stays in the inbox. We want it to remain unread.
                                # The fetch marked it as read, so we REMOVE the \Seen flag.
                                logging.info(f"Email ID {email_id.decode()} classified as Personal. Marking as UNREAD in {SOURCE_INBOX}.")
                                mail_connection.store(email_id, '-FLAGS', '(\\Seen)')

                            elif category == "Accounts":
                                # This email will be moved. We want it to be UNREAD in the destination folder.
                                # To do this, we mark it as unread *before* we copy it.
                                logging.info(f"Email ID {email_id.decode()} classified as Accounts. Marking as UNREAD before moving.")
                                mail_connection.store(email_id, '-FLAGS', '(\\Seen)')
                                if move_email(mail_connection, email_id, destination_folder):
                                    logging.info(f"Successfully moved email ID {email_id.decode()} to '{destination_folder}' as unread.")
                                else:
                                    logging.error(f"Failed to move email ID {email_id.decode()} to '{destination_folder}'.")

                            elif category in ["Spam", "Promotions"]:
                                # For these, we are okay with them being marked as read.
                                # The initial fetch already marked it as '\Seen', so we just move it.
                                logging.info(f"Email ID {email_id.decode()} classified as {category}. Moving as read.")
                                if move_email(mail_connection, email_id, destination_folder):
                                    logging.info(f"Successfully moved email ID {email_id.decode()} to '{destination_folder}'.")
                                else:
                                    logging.error(f"Failed to move email ID {email_id.decode()} to '{destination_folder}'.")

                            # Add delay after processing each email
                            logging.info(f"Waiting for {PROCESS_DELAY_SECONDS} seconds...")
                            time.sleep(PROCESS_DELAY_SECONDS)

                        except Exception as e_proc:
                            logging.error(f"An error occurred while processing email ID {email_id.decode()}: {e_proc}", exc_info=True)


        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, ConnectionResetError) as e:
            logging.error(f"Main loop IMAP connection issue: {e}. Attempting to re-establish connection.")
            if mail_connection:
                try: mail_connection.logout()
                except Exception: pass
            mail_connection = None
            time.sleep(60)
        except KeyboardInterrupt:
            logging.info("Script interrupted by user. Shutting down...")
            break
        except Exception as e:
            logging.critical(f"An unhandled critical error occurred in the main loop: {e}", exc_info=True)
            if mail_connection:
                try: mail_connection.logout()
                except Exception: pass
            mail_connection = None
            logging.info(f"Restarting loop after 60 seconds due to critical error.")
            time.sleep(60)

    if mail_connection and is_imap_connected(mail_connection):
        try:
            logging.info("Logging out from IMAP server.")
            mail_connection.close()
            mail_connection.logout()
        except Exception as e:
            logging.error(f"Error during final logout: {e}")
    logging.info("Email classification script finished.")

if __name__ == '__main__':
    # --- PRE-RUN CHECKS ---
    if not os.getenv('EMAIL_PASSWORD'):
        print("ERROR: EMAIL_PASSWORD environment variable not set. Please set it before running.")
        print("Example: export EMAIL_PASSWORD='your_actual_password'")
        exit(1)
    if not os.getenv('GEMINI_API_KEY'):
        print("ERROR: GEMINI_API_KEY environment variable not set. Please set it before running.")
        print("Example: export GEMINI_API_KEY='your_gemini_api_key'")
        exit(1)

    # Check if essential destination folders are likely to cause issues
    if FOLDER_MAPPING["Accounts"] == "Accounts":
        print(f"INFO: Ensure the IMAP folder '{FOLDER_MAPPING['Accounts']}' exists on your email server.")
    if FOLDER_MAPPING["Spam"].lower() not in ["spam", "[gmail]/spam", "inbox.spam"]:
        print(f"INFO: Ensure the IMAP folder '{FOLDER_MAPPING['Spam']}' exists and is correctly named.")
    if FOLDER_MAPPING["Promotions"].lower() not in ["junk", "junk e-mail", "[gmail]/junk"]:
        print(f"INFO: Ensure the IMAP folder '{FOLDER_MAPPING['Promotions']}' exists and is correctly named.")

    main()
