"""Gmail over IMAP (DESIGN.md §10): copy new inbox mail into source_message, archive what
email-sort called noise, and drop old excerpts.

Each account signs in with a Gmail app password from MAIL_ACCOUNTS. Messages are fetched
with BODY.PEEK, so syncing never marks anything read. The first sync of an account takes
the last FIRST_SYNC_DAYS days; after that, everything above the last UID seen.

Nothing here follows anything a message says. Bodies become a short plain-text excerpt
for the sweep, which treats it as data.
"""

import email
import email.policy
import imaplib
import logging
import re
from dataclasses import dataclass
from datetime import timedelta, timezone
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from sqlmodel import Session, col, select

from app.config import MailAccount, get_settings
from app.models import SourceMessage, SyncState, utcnow

log = logging.getLogger("table.sync")

HOST = "imap.gmail.com"
FIRST_SYNC_DAYS = 7
MAX_PER_SYNC = 200  # a backlog drains over a few runs instead of one long one
MAX_BYTES = 5_000_000  # bigger than this: headers only
EXCERPT_CHARS = 4000
NOISE_LABEL = "Table/Noise"


@dataclass
class RawMessage:
    uid: int
    gm_msgid: str
    gm_thrid: str
    data: bytes  # the full message, or just its headers when it was too big


class GmailClient:
    """The few IMAP calls the sync needs, so tests can swap in a fake."""

    def __init__(self, account: MailAccount):
        self.account = account
        self.imap: imaplib.IMAP4_SSL | None = None

    def __enter__(self):
        self.imap = imaplib.IMAP4_SSL(HOST, timeout=60)
        self.imap.login(self.account.address, self.account.password)
        return self

    def __exit__(self, *exc):
        try:
            self.imap.logout()
        except Exception:
            pass

    def _ok(self, result) -> list:
        status, data = result
        if status != "OK":
            raise RuntimeError(f"IMAP said {status}")
        return data

    def select_inbox(self, readonly: bool = True) -> str:
        self._ok(self.imap.select("INBOX", readonly=readonly))
        _, data = self.imap.response("UIDVALIDITY")  # ("UIDVALIDITY", [b"123"]), not ("OK", ...)
        if not data or data[0] is None:
            raise RuntimeError("IMAP gave no UIDVALIDITY")
        return data[0].decode()

    def uids_after(self, last_uid: int | None, since_days: int) -> list[int]:
        if last_uid is None:
            since = (utcnow() - timedelta(days=since_days)).strftime("%d-%b-%Y")
            data = self._ok(self.imap.uid("SEARCH", None, f"SINCE {since}"))
        else:
            data = self._ok(self.imap.uid("SEARCH", None, f"UID {last_uid + 1}:*"))
        uids = sorted(int(u) for u in (data[0] or b"").split())
        # "UID n:*" always returns the newest message, even when it is n-1 or lower.
        return [u for u in uids if last_uid is None or u > last_uid]

    def fetch(self, uid: int) -> RawMessage | None:
        meta = self._ok(self.imap.uid("FETCH", str(uid), "(X-GM-MSGID X-GM-THRID RFC822.SIZE)"))
        line = meta[0].decode() if isinstance(meta[0], bytes) else (meta[0] or b"")[0].decode()
        msgid = re.search(r"X-GM-MSGID (\d+)", line)
        thrid = re.search(r"X-GM-THRID (\d+)", line)
        size = re.search(r"RFC822\.SIZE (\d+)", line)
        if not msgid:
            return None
        part = "BODY.PEEK[HEADER]" if size and int(size.group(1)) > MAX_BYTES else "BODY.PEEK[]"
        body = self._ok(self.imap.uid("FETCH", str(uid), f"({part})"))
        data = next((b[1] for b in body if isinstance(b, tuple)), b"")
        return RawMessage(uid, msgid.group(1), thrid.group(1) if thrid else "", data)

    def archive(self, gm_msgid: str, label: str) -> bool:
        """Label one message and take it out of the inbox. False if it's no longer there."""
        self.imap.create(f'"{label}"')  # already exists after the first time; that's fine
        found = self._ok(self.imap.uid("SEARCH", None, f"X-GM-MSGID {gm_msgid}"))
        uids = (found[0] or b"").split()
        if not uids:
            return False
        uid = uids[0].decode()
        self._ok(self.imap.uid("STORE", uid, "+X-GM-LABELS", f'("{label}")'))
        self._ok(self.imap.uid("STORE", uid, "-X-GM-LABELS", r"(\Inbox)"))
        return True


# --- turning a message into a row ------------------------------------------------------------


class _Text(HTMLParser):
    """HTML to plain text: drops scripts, styles and every tag."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return "".join(parser.parts)


def _squash(text: str) -> str:
    text = re.sub(r"[ \t ​‌͏]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def excerpt(msg: EmailMessage) -> tuple[str, bool]:
    """The message's text, plain part preferred, cut to EXCERPT_CHARS; and whether it has
    attachments."""
    plain = html = None
    attachments = False
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            attachments = True
            continue
        kind = part.get_content_type()
        try:
            if kind == "text/plain" and plain is None:
                plain = part.get_content()
            elif kind == "text/html" and html is None:
                html = part.get_content()
        except (LookupError, ValueError):
            continue
    text = plain if plain and plain.strip() else html_to_text(html) if html else ""
    return _squash(text)[:EXCERPT_CHARS], attachments


def to_row(raw: RawMessage, account: MailAccount) -> SourceMessage:
    msg = email.message_from_bytes(raw.data, policy=email.policy.default)
    senders = getaddresses([str(msg.get("From", ""))])
    from_name, from_addr = senders[0] if senders else ("", "")
    to = getaddresses([str(msg.get("To", "")), str(msg.get("Cc", ""))])
    try:
        sent = parsedate_to_datetime(str(msg.get("Date", "")))
        # A date with no zone ("-0000") comes back naive; RFC 5322 says that means UTC.
        sent_at = sent.astimezone(timezone.utc).replace(tzinfo=None) if sent.tzinfo else sent
    except (TypeError, ValueError, IndexError):
        sent_at = None
    text, attachments = excerpt(msg) if msg.get_content_type() != "text/rfc822-headers" else ("", False)
    return SourceMessage(
        space=account.space,
        account=account.address,
        gm_msgid=raw.gm_msgid,
        gm_thrid=raw.gm_thrid,
        message_id=str(msg.get("Message-ID", ""))[:300],
        from_name=from_name[:200],
        from_addr=from_addr.lower()[:200],
        to_addrs=", ".join(a for _, a in to if a)[:500],
        subject=str(msg.get("Subject", ""))[:300],
        sent_at=sent_at,
        excerpt=text,
        has_attachments=attachments,
    )


# --- the sync --------------------------------------------------------------------------------


def _state(session: Session, key: str) -> SyncState:
    return session.get(SyncState, key) or SyncState(key=key)


def sync_account(session: Session, account: MailAccount, client) -> int:
    """Copy new inbox mail for one account. Returns how many messages were added."""
    key = f"mail:{account.address}"
    state = _state(session, key)
    validity = client.select_inbox()
    last_validity, _, last_uid = state.value.partition(":")
    # UIDs only mean something within one UIDVALIDITY; if Gmail reset it, start over
    # (the gm_msgid check below keeps that from adding duplicates).
    since_uid = int(last_uid) if last_uid and last_validity == validity else None
    added = 0
    uids = client.uids_after(since_uid, FIRST_SYNC_DAYS)[:MAX_PER_SYNC]
    for uid in uids:
        raw = client.fetch(uid)
        if raw is None:
            continue
        seen = session.exec(
            select(SourceMessage.id).where(SourceMessage.account == account.address, SourceMessage.gm_msgid == raw.gm_msgid)
        ).first()
        if seen is None:
            session.add(to_row(raw, account))
            added += 1
        state.value = f"{validity}:{uid}"
    if not uids and not state.value:
        state.value = f"{validity}:"
    state.last_ok_at, state.last_error, state.updated_at = utcnow(), "", utcnow()
    session.add(state)
    session.commit()
    return added


def archive_noise(session: Session, account: MailAccount, client) -> int:
    """Move messages sorted as noise out of the Gmail inbox, under Table/Noise."""
    pending = session.exec(
        select(SourceMessage).where(
            SourceMessage.account == account.address,
            SourceMessage.sort == "noise",
            col(SourceMessage.archived_at).is_(None),
        )
    ).all()
    if not pending:
        return 0
    client.select_inbox(readonly=False)
    moved = 0
    for message in pending:
        if client.archive(message.gm_msgid, NOISE_LABEL):
            moved += 1
        message.archived_at = utcnow()  # gone from the inbox either way; don't retry forever
        session.add(message)
    session.commit()
    return moved


def forget_old_excerpts(session: Session, keep_days: int) -> int:
    cutoff = utcnow() - timedelta(days=keep_days)
    old = session.exec(
        select(SourceMessage).where(col(SourceMessage.created_at) < cutoff, SourceMessage.excerpt != "")
    ).all()
    for message in old:
        message.excerpt = ""
        session.add(message)
    session.commit()
    return len(old)


def sync_all(session: Session, client_for=GmailClient) -> dict:
    """Every configured account. One account failing doesn't stop the others; the error
    is kept in sync_state (without the password) for the settings page."""
    settings = get_settings()
    report = {}
    for account in settings.mail_account_list:
        try:
            with client_for(account) as client:
                added = sync_account(session, account, client)
                archived = archive_noise(session, account, client) if settings.mail_archive_noise else 0
            report[account.address] = {"added": added, "archived": archived}
        except Exception as exc:  # noqa: BLE001  (record it and move on to the next account)
            session.rollback()
            # Gmail's reason ("[AUTHENTICATIONFAILED] Invalid credentials", "IMAP is
            # disabled") is what tells Kev what to fix. It never holds the password, but
            # mask it anyway in case a library ever echoes its input.
            reason = f"{type(exc).__name__}: {str(exc)[:200]}".replace(account.password, "***")
            state = _state(session, f"mail:{account.address}")
            state.last_error, state.updated_at = reason, utcnow()
            session.add(state)
            session.commit()
            log.warning("mail sync failed for %s: %s", account.address, reason)
            report[account.address] = {"error": reason}
    report["excerpts_forgotten"] = forget_old_excerpts(session, settings.mail_keep_days)
    return report
