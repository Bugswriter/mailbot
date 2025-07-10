# 📬 mailbot ai

`mailbot-ai` is a Python-based email automation bot that connects to your inbox via IMAP, reads unread messages, and uses **Gemini AI** to intelligently classify and summarize them.

---

## ⚙️ Requirements

- Python 3.8+
- A `.env` file with your credentials and Gemini API key

---

## 📦 Setup

1. **Clone the repository:**

   ```bash
   git clone https://github.com/Bugswriter/mailbot.git
   cd mailbot
    ````

2. **Create a virtual environment and activate it:**

   ```bash
   python -m venv venv
   source venv/bin/activate  # For Linux/macOS
   ```

3. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

4. **Create a `.env` file in the root directory:**

   ```env
   EMAIL_USERNAME=your_email@example.com
   EMAIL_PASSWORD=your_email_password_or_app_password
   IMAP_SERVER=imap.your-email-provider.com
   GEMINI_API_KEY=your_gemini_api_key
   ```

---

## 🤖 Features

* Connects to your inbox via IMAP
* Uses **Gemini AI** to:

  * Classify emails into types like Work, Personal, Spam, etc.
  * Optionally summarize the content
* Fully customizable prompt (see `prompt_template.py`)

> ✏️ You can modify the AI prompt logic in `prompt_template.py` to change classification behavior or format.

---

## 🚀 Usage

```bash
python main.py
```

---

## 🛠️ Optional: Run as a systemd Service

To keep the bot running in the background:

```ini
# /etc/systemd/system/mailbot.service
[Unit]
Description=MailBot - AI Email Classifier
After=network.target

[Service]
User=your_username
WorkingDirectory=/path/to/mailbot
ExecStart=/path/to/mailbot/venv/bin/python /path/to/mailbot/main.py
Restart=always
Environment="EMAIL_USERNAME=your_email"
Environment="EMAIL_PASSWORD=your_password"
Environment="IMAP_SERVER=imap.server"
Environment="GEMINI_API_KEY=your_key"

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reexec
sudo systemctl enable --now mailbot.service
```

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0**.
