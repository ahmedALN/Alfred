"""Alfred and your inbox.

The rule this is built around, decided before a line of it was written:
Alfred may read, sort and draft, and may never send. That is not
enforced by this code being careful. It is enforced by the scope Alfred
asks Google for - gmail.modify covers reading, labelling, archiving and
creating drafts, and does not cover sending. Alfred could not send an
email if it tried, and neither could a bug in it.

Deleting is absent for the same reason and by the same means: modify
cannot permanently delete anything. The worst Alfred can do to a message
is take it out of the inbox, which is undoable from any phone.
"""

from src.mail.gmail import Gmail, MailError

__all__ = ["Gmail", "MailError"]
