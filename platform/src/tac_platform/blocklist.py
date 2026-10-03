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


async def is_blocked(tx, secret: str, github_id: int | None, handle: str) -> bool:
    """`tx` = a Tx or the Database: call it inside the transaction that creates the user."""
    return await tx.fetchone("SELECT 1 FROM blocked_identities WHERE github_id_hash = ?",
                             (identity_hash(secret, github_id, handle),)) is not None
