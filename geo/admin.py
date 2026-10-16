"""
Bootstrap access from the command line, before the web app's admin screens
exist. Users must have signed up (Supabase Auth) first: that's what creates
their profiles row.
"""
from sqlalchemy import func, select

from geo.models import MEMBER_ROLES, Client, ClientMember, Invitation, Profile


class AdminError(Exception):
    pass


def _profile(session, email):
    p = session.scalar(select(Profile).where(func.lower(Profile.email) == email.strip().lower()))
    if p is None:
        raise AdminError(f"No user with email {email!r}. They need to sign up in the app first.")
    return p


def set_role(session, email, role):
    if role not in ("admin", "client"):
        raise AdminError("role must be 'admin' or 'client'")
    p = _profile(session, email)
    p.role = role
    return p


def add_member(session, email, client_name, role="viewer"):
    if role not in MEMBER_ROLES:
        raise AdminError(f"role must be one of {MEMBER_ROLES}")
    p = _profile(session, email)
    client = session.scalar(select(Client).where(Client.name == client_name))
    if client is None:
        names = ", ".join(session.scalars(select(Client.name).order_by(Client.name)))
        raise AdminError(f"No client named {client_name!r}. Known clients: {names or '(none)'}")
    m = session.get(ClientMember, (p.id, client.id))
    if m is None:
        m = ClientMember(user_id=p.id, client_id=client.id, role=role)
        session.add(m)
    else:
        m.role = role
    return m


def remove_member(session, email, client_name):
    p = _profile(session, email)
    client = session.scalar(select(Client).where(Client.name == client_name))
    m = client and session.get(ClientMember, (p.id, client.id))
    if m:
        session.delete(m)
    return bool(m)


def invite(session, email, role="admin", client_name=None, member_role="viewer"):
    """Grant access by email whether or not they've signed up yet. Existing
    users get it immediately; new ones get it on first sign-in (via the
    invitations table + sign-up trigger). Returns 'applied' or 'invited'."""
    email = email.strip().lower()
    if role not in ("admin", "client"):
        raise AdminError("role must be 'admin' or 'client'")
    client = None
    if role == "client":
        if member_role not in MEMBER_ROLES:
            raise AdminError(f"member role must be one of {MEMBER_ROLES}")
        client = session.scalar(select(Client).where(Client.name == client_name))
        if client is None:
            raise AdminError(f"No client named {client_name!r}.")

    profile = session.scalar(select(Profile).where(func.lower(Profile.email) == email))
    if profile is not None:
        if role == "admin":
            set_role(session, email, "admin")
        else:
            add_member(session, email, client.name, member_role)
        return "applied"

    client_id = client.id if client else None
    existing = session.scalar(select(Invitation).where(
        Invitation.email == email,
        Invitation.client_id.is_(None) if client_id is None else Invitation.client_id == client_id))
    if existing is None:
        session.add(Invitation(email=email, role=role, client_id=client_id,
                               member_role=member_role if client else None))
    else:
        existing.role, existing.member_role = role, (member_role if client else None)
    return "invited"
