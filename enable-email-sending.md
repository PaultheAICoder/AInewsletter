# Enable Newsletter Email Sending

This document outlines the steps to enable automated newsletter sending via `hello@aireadypdx.com`.

## Current Status

- **Newsletter Generation**: Working (runs Friday 6 AM, saves to database + HTML)
- **Email Sending**: Disabled (commented out in cron, missing SMTP password)
- **Subscribers**: 4 active subscribers configured
  - paulinpdx503@gmail.com (Paul Brown)
  - pbrown@vital-enterprises.com (Paul Brown)
  - rhoppes@vital-enterprises.com (Ryan Hoppes)
  - johnwebber@novuslabs.com (John Webber)

---

## Steps for Paul (Human)

### 1. Create Microsoft 365 App Password

App passwords are required because SMTP authentication doesn't support modern auth (OAuth).

1. **Sign in** to Microsoft 365:
   - Go to https://myaccount.microsoft.com
   - Sign in as `hello@aireadypdx.com`

2. **Enable 2FA** (if not already):
   - Go to **Security info** or **Security** → **Additional security options**
   - Set up multi-factor authentication (required for app passwords)

3. **Create App Password**:
   - In Security settings, find **App passwords**
   - Click **Create a new app password**
   - Name it: `AI Newsletter SMTP`
   - **Copy the 16-character password** (shown only once!)

4. **Update .env file** on the server:
   ```bash
   # SSH to server and edit .env
   nano /home/pbrown/AInewsletter/.env

   # Find and update this line:
   SMTP_PASSWORD=xxxx xxxx xxxx xxxx
   ```
   Replace `<APP_PASSWORD_HERE>` with the actual 16-character password.

5. **Tell Claude** you've updated the password so I can enable sending.

### 2. (Optional) Verify SMTP Settings

Current configuration in `.env`:
```
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USER=hello@aireadypdx.com
SMTP_FROM_EMAIL=hello@aireadypdx.com
SMTP_FROM_NAME=AI Ready PDX
```

If `hello@aireadypdx.com` uses a different mail provider, let me know and I'll update.

---

## Steps for Claude (AI)

### 1. Test SMTP Connection (after password is set)

```bash
source .venv/bin/activate
python -c "
from src.newsletter.sender import EmailSender
sender = EmailSender()
print('SMTP connection successful!')
"
```

### 2. Test Send with Dry Run

```bash
python scripts/send_newsletter.py --issue-id 4 --dry-run --verbose
```

This will simulate sending without actually sending emails.

### 3. Send a Real Test Email

```bash
python scripts/send_newsletter.py --issue-id 4 --verbose
```

### 4. Enable Sending in Cron Job

Edit `/home/pbrown/AInewsletter/scripts/cron_newsletter.sh`:

**Current (disabled):**
```bash
# Step 2: Send newsletter (uncomment when SMTP is configured)
# echo "[$(date)] Sending newsletter issue #$ISSUE_ID..." >> "$LOG_FILE"
# python scripts/send_newsletter.py --issue-id "$ISSUE_ID" 2>&1 | tee -a "$LOG_FILE"
```

**After enabling:**
```bash
# Step 2: Send newsletter
echo "[$(date)] Sending newsletter issue #$ISSUE_ID..." >> "$LOG_FILE"
python scripts/send_newsletter.py --issue-id "$ISSUE_ID" 2>&1 | tee -a "$LOG_FILE"
```

### 5. Commit Changes

```bash
git add scripts/cron_newsletter.sh
git commit -m "Enable newsletter email sending"
git push
```

---

## Verification Checklist

After setup is complete:

- [ ] App password created in Microsoft 365
- [ ] `.env` updated with real SMTP_PASSWORD
- [ ] Test SMTP connection works
- [ ] Dry run send completes successfully
- [ ] Real test email received by at least one subscriber
- [ ] Cron script updated to enable sending
- [ ] Changes committed and pushed

---

## Troubleshooting

### "Authentication failed" error
- Verify app password is correct (no extra spaces)
- Ensure 2FA is enabled on the Microsoft account
- Check that `hello@aireadypdx.com` has SMTP sending permissions

### "Connection refused" error
- Verify SMTP_HOST and SMTP_PORT are correct
- Check if firewall allows outbound port 587

### Emails going to spam
- Ensure SPF/DKIM/DMARC records are configured for aireadypdx.com
- Consider adding subscribers to safe sender list initially

---

## Files Involved

| File | Purpose |
|------|---------|
| `.env` | SMTP credentials (password needs updating) |
| `scripts/cron_newsletter.sh` | Weekly cron job (sending currently disabled) |
| `scripts/send_newsletter.py` | CLI to send a newsletter issue |
| `src/newsletter/sender.py` | SMTP email sending logic |
| `src/newsletter/email_builder.py` | Builds personalized HTML emails |

---

*Document created: 2025-12-13*
