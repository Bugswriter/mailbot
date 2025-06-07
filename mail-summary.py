#!/usr/bin/env python3

import imaplib
import getpass
import sys
import re

def login_to_imap(server, email, password):
    try:
        imap = imaplib.IMAP4_SSL(server)
        imap.login(email, password)
        return imap
    except imaplib.IMAP4.error as e:
        print("❌ Login failed:", e)
        sys.exit(1)

def clean_folder_name(raw_line):
    # Extract folder name using regex
    match = re.search(r'\"(.+?)\"$', raw_line)
    if match:
        return match.group(1)
    else:
        return raw_line.split()[-1].strip('"')

def get_mailbox_stats(imap):
    status, mailboxes = imap.list()
    if status != 'OK' or not mailboxes:
        print("❌ No folders found or failed to fetch.")
        return

    print("\n📬 Mail folders summary:\n")
    for mailbox in mailboxes:
        decoded = mailbox.decode()
        folder = clean_folder_name(decoded)

        try:
            imap.select(f'"{folder}"', readonly=True)
            status_total, data_total = imap.search(None, 'ALL')
            status_unread, data_unread = imap.search(None, 'UNSEEN')

            total = len(data_total[0].split()) if status_total == 'OK' else 0
            unread = len(data_unread[0].split()) if status_unread == 'OK' else 0

            print(f"{folder} - Total: {total}, Unread: {unread}")
        except Exception as e:
            print(f"{folder} - ❌ Error: {e}")

def main():
    print("📧 IMAP Email Folder Summary Script\n")

    server = input("IMAP Server (e.g., imap.gmail.com): ").strip()
    email = input("Email/Username: ").strip()
    password = getpass.getpass("Password: ")

    imap = login_to_imap(server, email, password)
    get_mailbox_stats(imap)
    imap.logout()

if __name__ == "__main__":
    main()

