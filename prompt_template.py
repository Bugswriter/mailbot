# prompt_template.py

CLASSIFICATION_PROMPT = """Analyze the following email's sender, subject, and body, then assign it one of the following four categories. Your response MUST be only one of these exact words: "Personal", "Spam", "Accounts", or "Promotions".

Category Definitions:
1. Personal: Emails written by a human directly to me. These are typically from friends, family, or colleagues for direct conversation, and are not automated or mass-sent.
2. Spam: Unsolicited, unwanted commercial emails, scams, phishing attempts, suspicious content, or anything clearly undesired.
3. Accounts: Transactional emails from services or businesses where I have an an active account. This includes bank statements, transaction alerts, invoices, order confirmations, shipping updates, password resets, OTPs, or critical subscription notifications.
4. Promotions: Marketing emails, newsletters, advertisements, sales announcements, or general non-critical updates from businesses or organizations that I might have subscribed to but are not essential transactional information.

If you are unsure or if the content is unclear, categorize it as "Personal".
If email id domain is gmail.com or zoho mail or yahoo mail or outlook always store it in Personal.

---
Email to classify:
Sender: {sender}
Subject: {subject}
Body:
{truncated_body}
---
Category:"""
