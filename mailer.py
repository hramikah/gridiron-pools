"""Outbound email for the app. Sends for real via Gmail SMTP once
mail_username/mail_app_password are set (Settings table, same pattern as
the odds API key -- never in tracked source). Falls back to dry-run
logging (logs/emails.log) if credentials aren't configured yet, or if a
real send fails, so a mail outage never breaks registration/picks flows.
"""

import logging
import os
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger("mailer")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "emails.log")


def _log_email(to_address, subject, body, note=""):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(f"--- to={to_address} | subject={subject}{note} ---\n{body}\n\n")
    logger.info("Email (dry-run, not actually sent): to=%s subject=%s", to_address, subject)


def _send(to_address, subject, body):
    from helpers import get_setting  # local import: avoids a helpers<->models<->mailer cycle at module load

    username = get_setting("mail_username")
    app_password = get_setting("mail_app_password")

    if not username or not app_password:
        _log_email(to_address, subject, body)
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = f"Gridiron Pools <{username}>"
    msg["To"] = to_address

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(username, app_password)
            server.sendmail(username, [to_address], msg.as_string())
        logger.info("Email sent: to=%s subject=%s", to_address, subject)
    except Exception:
        logger.exception("Real send failed, falling back to dry-run log: to=%s subject=%s", to_address, subject)
        _log_email(to_address, subject, body, note=" [SEND FAILED, see server log]")


def _send_bulk(recipient_addresses, subject, body):
    """Send the same message to many recipients over a single SMTP
    connection (much faster than one connection per email). Falls back to
    dry-run logging for everyone if credentials aren't configured, and
    logs+skips any individual recipient that fails rather than aborting
    the whole batch."""
    from helpers import get_setting

    username = get_setting("mail_username")
    app_password = get_setting("mail_app_password")

    if not username or not app_password:
        for addr in recipient_addresses:
            _log_email(addr, subject, body)
        return

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(username, app_password)
            for addr in recipient_addresses:
                msg = MIMEText(body)
                msg["Subject"] = subject
                msg["From"] = f"Gridiron Pools <{username}>"
                msg["To"] = addr
                try:
                    server.sendmail(username, [addr], msg.as_string())
                    logger.info("Bulk email sent: to=%s subject=%s", addr, subject)
                except Exception:
                    logger.exception("Bulk send failed for one recipient: to=%s subject=%s", addr, subject)
                    _log_email(addr, subject, body, note=" [SEND FAILED, see server log]")
    except Exception:
        logger.exception("Bulk send SMTP connection failed entirely")
        for addr in recipient_addresses:
            _log_email(addr, subject, body, note=" [SEND FAILED, connection error]")


def send_announcement_email(author_username, announcement_body, recipient_addresses):
    subject = "New Announcement - Gridiron Pools"
    body = (
        f"{author_username} posted a new announcement:\n\n"
        f"{announcement_body}\n\n"
        "-- Gridiron Pools"
    )
    _send_bulk(recipient_addresses, subject, body)


def send_admin_notification_email(sender_username, message_body, admin_addresses):
    subject = f"New message from {sender_username} - Gridiron Pools"
    body = (
        f"{sender_username} sent a message on the Message Board:\n\n"
        f"{message_body}\n\n"
        "-- Gridiron Pools"
    )
    _send_bulk(admin_addresses, subject, body)


def send_welcome_email(user):
    subject = "Welcome to Gridiron Pools!"
    body = (
        f"Hi {user.username},\n\n"
        "You're all set up on Gridiron Pools. Log in any time to join the "
        "Drop Dead Pool, Loser Pool, or Gridiron Investments and start "
        "making your picks.\n\n"
        "-- Gridiron Pools"
    )
    _send(user.email, subject, body)


def send_picks_recap_email(user, week, recap_body):
    subject = f"Your Week {week.number} Picks - Gridiron Pools"
    body = f"Hi {user.username},\n\nHere's a recap of your locked-in picks for Week {week.number}:\n\n{recap_body}\n\n-- Gridiron Pools"
    _send(user.email, subject, body)


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


