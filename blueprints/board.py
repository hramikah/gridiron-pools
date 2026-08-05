import threading

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from helpers import admin_required
from mailer import send_admin_notification_email, send_announcement_email
from models import Announcement, ContactMessage, User, db

bp = Blueprint("board", __name__)


def _send_async(fn, *args):
    """Run a mailer call on a background thread with its own app context,
    so the request that triggered it (posting an announcement, sending a
    contact message) returns immediately instead of waiting on SMTP."""
    app = current_app._get_current_object()

    def run():
        with app.app_context():
            fn(*args)

    threading.Thread(target=run, daemon=True).start()


@bp.route("/")
@login_required
def index():
    announcements = Announcement.query.order_by(Announcement.created_at.desc()).all()
    return render_template("board/index.html", announcements=announcements)


@bp.route("/announce", methods=["POST"])
@login_required
@admin_required
def announce():
    body = request.form.get("body", "").strip()
    if not body:
        flash("Announcement can't be empty.", "error")
        return redirect(url_for("board.index"))
    announcement = Announcement(author_id=current_user.id, body=body)
    db.session.add(announcement)
    db.session.commit()

    recipient_addresses = [u.email for u in User.query.all()]
    _send_async(send_announcement_email, current_user.username, body, recipient_addresses)

    flash("Announcement posted and emailed to all players.", "success")
    return redirect(url_for("board.index"))


@bp.route("/contact", methods=["POST"])
@login_required
def contact():
    body = request.form.get("body", "").strip()
    if not body:
        flash("Message can't be empty.", "error")
        return redirect(url_for("board.index"))
    message = ContactMessage(user_id=current_user.id, body=body)
    db.session.add(message)
    db.session.commit()

    admin_addresses = [u.email for u in User.query.filter_by(is_admin=True).all()]
    _send_async(send_admin_notification_email, current_user.username, body, admin_addresses)

    flash("Your message was sent to the admin.", "success")
    return redirect(url_for("board.index"))
