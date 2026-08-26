from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from helpers import get_setting, log_activity, send_async
from mailer import send_admin_message_email
from models import Announcement, ContactMessage, User, db

bp = Blueprint("board", __name__)


@bp.route("/")
@login_required
def index():
    announcements = Announcement.query.order_by(Announcement.created_at.desc()).all()
    my_thread = (
        ContactMessage.query.filter_by(user_id=current_user.id)
        .order_by(ContactMessage.created_at.asc())
        .all()
    )
    # Opening the board reads what's waiting for *you* in your own thread:
    # messages someone else wrote. Never your own -- a player marking their own
    # message read would clear the admin's unread badge before the admin ever
    # saw it. The one exception is an admin's own thread: nothing else on
    # either side would ever clear a note they left there themselves.
    unread_here = [
        m
        for m in my_thread
        if not m.is_read
        and (m.sender_id != current_user.id or current_user.is_admin)
    ]
    if unread_here:
        for m in unread_here:
            m.is_read = True
        db.session.commit()
    # Admins are the other side of every player's thread, so the board has to
    # surface those here too -- filtering on user_id alone only ever returned
    # the admin's own thread, which is why player messages never appeared.
    player_threads = []
    if current_user.is_admin:
        threads = {}
        for m in ContactMessage.query.order_by(ContactMessage.created_at.asc()).all():
            if m.user_id == current_user.id:
                continue  # the admin's own thread is already shown above
            threads.setdefault(m.user_id, []).append(m)
        for msgs in threads.values():
            player_threads.append({
                "user": msgs[-1].user,
                "last": msgs[-1],
                "count": len(msgs),
                "unread": sum(1 for m in msgs if not m.from_admin and not m.is_read),
            })
        # unread first, then most recently active
        player_threads.sort(key=lambda r: (r["unread"] == 0, -r["last"].created_at.timestamp()))

    return render_template(
        "board/index.html",
        announcements=announcements,
        my_thread=my_thread,
        player_threads=player_threads,
    )


@bp.route("/contact", methods=["POST"])
@login_required
def contact():
    body = request.form.get("body", "").strip()
    if not body:
        flash("Message can't be empty.", "error")
        return redirect(url_for("board.index"))
    message = ContactMessage(user_id=current_user.id, sender_id=current_user.id, body=body)
    db.session.add(message)
    db.session.commit()
    log_activity("message_sent", f"Messaged the commissioners: {body[:120]}")

    # Tell the commissioners. Nothing on the site nudges them otherwise, so a
    # message could sit unread for days. Sent in the background so a slow mail
    # API never holds up the post itself, and to every admin with an address --
    # whoever picks it up first can answer.
    admin_addresses = [
        u.email for u in User.query.filter_by(is_admin=True).all() if u.email
    ]
    if admin_addresses:
        site_url = get_setting("site_url", "")
        link = f"{site_url}{url_for('admin.message_thread', user_id=current_user.id)}"
        send_async(send_admin_message_email, admin_addresses, current_user.username, body, link)

    flash("Your message was sent to the commissioners.", "success")
    return redirect(url_for("board.index"))
