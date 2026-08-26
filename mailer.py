"""Outbound email for the app. Sends for real via SendGrid's HTTP API once
sendgrid_api_key/sendgrid_from_email are set (Settings table, same pattern
as the odds API key -- never in tracked source). Uses HTTPS (port 443), not
SMTP -- DigitalOcean and most cloud providers block outbound SMTP ports
(25/465/587) by default and won't lift it, so plain smtplib doesn't work
from this droplet. Falls back to dry-run logging (logs/emails.log) if
credentials aren't configured yet, or if a real send fails, so a mail
outage never breaks registration/picks flows.
"""

import logging
import os

import requests

logger = logging.getLogger("mailer")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "emails.log")

SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"


def _log_email(to_address, subject, body, note=""):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(f"--- to={to_address} | subject={subject}{note} ---\n{body}\n\n")
    logger.info("Email (dry-run, not actually sent): to=%s subject=%s", to_address, subject)


def _sendgrid_request(to_address, subject, body, api_key, from_email):
    payload = {
        "personalizations": [{"to": [{"email": to_address}]}],
        "from": {"email": from_email, "name": "Gridiron Pools"},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    resp = requests.post(
        SENDGRID_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()


def _send(to_address, subject, body):
    from helpers import get_setting  # local import: avoids a helpers<->models<->mailer cycle at module load

    api_key = get_setting("sendgrid_api_key")
    from_email = get_setting("sendgrid_from_email")

    if not api_key or not from_email:
        _log_email(to_address, subject, body)
        return

    try:
        _sendgrid_request(to_address, subject, body, api_key, from_email)
        logger.info("Email sent: to=%s subject=%s", to_address, subject)
    except Exception:
        logger.exception("Real send failed, falling back to dry-run log: to=%s subject=%s", to_address, subject)
        _log_email(to_address, subject, body, note=" [SEND FAILED, see server log]")


def _send_bulk(recipient_addresses, subject, body):
    """Send the same message to many recipients. Falls back to dry-run
    logging for everyone if credentials aren't configured, and logs+skips
    any individual recipient that fails rather than aborting the whole
    batch."""
    from helpers import get_setting

    api_key = get_setting("sendgrid_api_key")
    from_email = get_setting("sendgrid_from_email")

    if not api_key or not from_email:
        for addr in recipient_addresses:
            _log_email(addr, subject, body)
        return

    for addr in recipient_addresses:
        try:
            _sendgrid_request(addr, subject, body, api_key, from_email)
            logger.info("Bulk email sent: to=%s subject=%s", addr, subject)
        except Exception:
            logger.exception("Bulk send failed for one recipient: to=%s subject=%s", addr, subject)
            _log_email(addr, subject, body, note=" [SEND FAILED, see server log]")






def send_password_reset_email(user, temp_password):
    subject = "Your Gridiron Pools password was reset"
    body = (
        f"Hi {user.username},\n\n"
        "An admin reset your Gridiron Pools password. Your temporary password is:\n\n"
        f"    {temp_password}\n\n"
        "Log in with it, then change it right away from the top-right menu -> "
        "Change Password.\n\n"
        "-- Gridiron Pools"
    )
    _send(user.email, subject, body)


def send_password_reset_link_email(email, username_links):
    """username_links: list of (username, reset_url) pairs. One email may
    have several accounts (one per entry), so a single message carries a
    separate single-use link for each."""
    subject = "Reset your Gridiron Pools password"
    if len(username_links) == 1:
        username, link = username_links[0]
        accounts = f"Account: {username}\n{link}\n"
    else:
        accounts = (
            "This email has more than one account. Use the link for the one "
            "you want to reset:\n\n"
            + "\n".join(f"Account: {u}\n{link}\n" for u, link in username_links)
        )
    body = (
        "Hi,\n\n"
        "Someone asked to reset the Gridiron Pools password for this email.\n\n"
        f"{accounts}\n"
        "The link works once and expires in 1 hour. If you didn't ask for "
        "this, you can ignore this email -- your password hasn't changed.\n\n"
        "-- Gridiron Pools"
    )
    _send(email, subject, body)


def send_invite_link_emails(email_links):
    """email_links: list of (email, registration_url) pairs, one unique
    single-use invite link per recipient -- registration is only possible
    through this link, so the site can't be joined by anyone else."""
    subject = "You are invited to Gridiron Pools"
    for email, link in email_links:
        body = (
            "Hi,\n\n"
            "You are invited to join Gridiron Pools -- our NFL pick'em pools: "
            "Drop Dead Pool, Loser Pool, and Gridiron Investments.\n\n"
            f"Register here: {link}\n\n"
            "This link is just for you -- head there to create your account and get started. "
            "See you in the pool!\n\n"
            "-- Gridiron Pools"
        )
        _send(email, subject, body)




def send_admin_message_email(admin_addresses, player_username, body, link):
    """Tell the commissioners a player has written to them.

    One message can reach several admins, so this goes through _send_bulk:
    a bad address for one commissioner must not stop the others hearing
    about it.
    """
    subject = f"New message from {player_username} - Gridiron Pools"
    text = (
        f"{player_username} sent the commissioners a message:\n\n"
        f"{body}\n\n"
        f"Reply here: {link}\n\n"
        "-- Gridiron Pools"
    )
    _send_bulk(admin_addresses, subject, text)


def send_player_message_email(user, body, link):
    """Tell a player the commissioners have replied to them."""
    subject = "The commissioners replied - Gridiron Pools"
    text = (
        f"Hi {user.username},\n\n"
        "The commissioners have replied to your message:\n\n"
        f"{body}\n\n"
        f"Read it and reply here: {link}\n\n"
        "-- Gridiron Pools"
    )
    _send(user.email, subject, text)
