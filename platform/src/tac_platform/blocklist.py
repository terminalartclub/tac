"""Identities barred from signing up again after an admin deleted their suspended account.

Deleting the user row would otherwise lift a suspension: the same GitHub login could re-register under the
freed handle. Only a salted hash of the identity is kept, never the GitHub id or login itself:
sha256(salt | identity), salt = sha256("tac-blocked-identities|" + the install secret, kv 'secret').
identity = "github:<numeric github id>" (prod), or "dev:<handle>" for TAC_AUTH=dev accounts (no GitHub id;
prod refuses dev auth). The column keeps the github_id_hash name from the design for both.
"""

import hashlib

from .web import sha256_hex


def _salt(secret: str) -> str:
    return hashlib.sha256(f"tac-blocked-identities|{secret}".encode()).hexdigest()


def identity_hash(secret: str, github_id: int | None, handle: str) -> str:
    identity = f"github:{github_id}" if github_id is not None else f"dev:{handle}"
    return sha256_hex(f"{_salt(secret)}|{identity}")


def account_pseudonym(secret: str, user_id: int, created_at: str, first_audit_id: int) -> str:
    """What an erased account's audit rows say instead of its handle: stable per account (rows still link up),
    distinct across accounts (SQLite can reuse a deleted user's rowid, even within the same second, so the
    account's first audit row id is part of it), and not reversible to the handle without the salt."""
    return "deleted:" + sha256_hex(f"{_salt(secret)}|acct:{user_id}:{created_at}:{first_audit_id}")[:12]


def slug_pseudonym(secret: str, account_pseud: str, slug: str) -> str:
    """An erased account's piece slug in the log: `<account pseudonym>/<12 hex>`. Stable within the account
    (one piece's rows still line up), and the slug (derived from the title) can't be read back or searched."""
    return f"{account_pseud}/" + sha256_hex(f"{_salt(secret)}|slug:{slug}|{account_pseud}")[:12]


async def is_blocked(tx, secret: str, github_id: int | None, handle: str) -> bool:
    """`tx` = a Tx or the Database: call it inside the transaction that creates the user."""
    return await tx.fetchone("SELECT 1 FROM blocked_identities WHERE github_id_hash = ?",
                             (identity_hash(secret, github_id, handle),)) is not None
