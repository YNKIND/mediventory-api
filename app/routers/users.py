from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.dependencies import get_db, get_current_user, require_admin, get_clinic_id
from app.email import send_invite, email_configured
from app.security import hash_password, generate_reset_token, reset_token_expiry, validate_password


router = APIRouter(prefix="/users", tags=["users"])

VALID_ROLES = {"owner", "admin", "staff"}


def assert_role_valid(role: str):
    if role not in VALID_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Role must be one of: {', '.join(sorted(VALID_ROLES))}")


def get_clinic_membership(db: Session, user_id: int, clinic_id: int) -> models.ClinicMembership:
    m = db.query(models.ClinicMembership).filter(
        models.ClinicMembership.user_id == user_id,
        models.ClinicMembership.clinic_id == clinic_id,
    ).first()
    if not m:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User is not a member of this clinic")
    return m


@router.get("", response_model=list[schemas.UserOut])
def list_users(
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    membership=Depends(require_admin),
):
    # Everyone with a membership in this clinic, with their role IN THIS CLINIC.
    memberships = db.query(models.ClinicMembership).filter(
        models.ClinicMembership.clinic_id == clinic_id
    ).all()

    result = []
    for m in memberships:
        user = db.query(models.User).filter(models.User.id == m.user_id).first()
        if not user:
            continue
        result.append({
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": m.role,          # role in THIS clinic
            "active": user.active,
        })
    result.sort(key=lambda r: r["full_name"].lower())
    return result


@router.post("", response_model=schemas.UserOut)
def create_user(
    payload: schemas.UserAdminCreate,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
    membership=Depends(require_admin),
):
    assert_role_valid(payload.role)
    if payload.role == "owner" and membership.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an owner can create another owner")

    email = payload.email.lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An account with that email already exists. Use 'Add existing user' to give them access to this clinic.")
    if not payload.full_name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Full name is required")

    if payload.password:
        password_error = validate_password(payload.password)
        if password_error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=password_error)
        password_hash = hash_password(payload.password)
    else:
        if not payload.send_invite:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Set a password or send an invite, otherwise this account cannot be used")
        password_hash = hash_password(generate_reset_token()[0])

    user = models.User(
        clinic_id=clinic_id,
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=password_hash,
        role=payload.role,
        active=True,
    )
    db.add(user)
    db.flush()

    # Membership in this clinic with the chosen role.
    db.add(models.ClinicMembership(user_id=user.id, clinic_id=clinic_id, role=payload.role))

    if payload.send_invite:
        raw_token, token_hash = generate_reset_token()
        db.add(models.PasswordResetToken(user_id=user.id, token_hash=token_hash, expires_at=reset_token_expiry()))
        db.commit()
        db.refresh(user)
        send_invite(user.email, user.full_name, raw_token, current_user.full_name)
    else:
        db.commit()
        db.refresh(user)

    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": payload.role, "active": user.active}


@router.post("/add-to-clinic", response_model=schemas.SimpleMessage)
def add_user_to_clinic(
    payload: schemas.AddToClinicRequest,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
    membership=Depends(require_admin),
):
    assert_role_valid(payload.role)
    if payload.role == "owner" and membership.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an owner can grant owner access")

    email = payload.email.lower()
    user = db.query(models.User).filter(models.User.email == email).first()

    if user:
        existing = db.query(models.ClinicMembership).filter(
            models.ClinicMembership.user_id == user.id,
            models.ClinicMembership.clinic_id == clinic_id,
        ).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That person already has access to this clinic")
        db.add(models.ClinicMembership(user_id=user.id, clinic_id=clinic_id, role=payload.role))
        db.commit()
        return {"message": f"{user.full_name} now has access to this clinic"}

    if not payload.full_name or not payload.full_name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This email has no account yet, so a full name is required to create one")

    new_user = models.User(
        clinic_id=clinic_id,
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(generate_reset_token()[0]),
        role=payload.role,
        active=True,
    )
    db.add(new_user)
    db.flush()
    db.add(models.ClinicMembership(user_id=new_user.id, clinic_id=clinic_id, role=payload.role))

    raw_token, token_hash = generate_reset_token()
    db.add(models.PasswordResetToken(user_id=new_user.id, token_hash=token_hash, expires_at=reset_token_expiry()))
    db.commit()

    send_invite(new_user.email, new_user.full_name, raw_token, current_user.full_name)
    return {"message": f"Invited {email} to this clinic"}


@router.patch("/{user_id}", response_model=schemas.UserOut)
def update_user(
    user_id: int,
    payload: schemas.UserAdminUpdate,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
    membership=Depends(require_admin),
):
    target = get_clinic_membership(db, user_id, clinic_id)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if payload.full_name is not None:
        if not payload.full_name.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Full name cannot be empty")
        user.full_name = payload.full_name.strip()

    # Role changes apply to the membership in THIS clinic.
    if payload.role is not None and payload.role != target.role:
        assert_role_valid(payload.role)
        if membership.role != "owner":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an owner can change roles")
        if user.id == current_user.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot change your own role")
        if target.role == "owner":
            remaining = db.query(models.ClinicMembership).filter(
                models.ClinicMembership.clinic_id == clinic_id,
                models.ClinicMembership.role == "owner",
                models.ClinicMembership.user_id != user.id,
            ).count()
            if remaining == 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="There must always be at least one owner in this clinic")
        target.role = payload.role

    if payload.active is not None and payload.active != user.active:
        if user.id == current_user.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot deactivate your own account")
        user.active = payload.active

    db.commit()
    db.refresh(user)
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": target.role, "active": user.active}


@router.post("/{user_id}/send-reset", response_model=schemas.SimpleMessage)
def send_reset_for_user(
    user_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
    membership=Depends(require_admin),
):
    get_clinic_membership(db, user_id, clinic_id)  # ensures they're in this clinic
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or not user.active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This account is deactivated")

    raw_token, token_hash = generate_reset_token()
    db.add(models.PasswordResetToken(user_id=user.id, token_hash=token_hash, expires_at=reset_token_expiry()))
    db.commit()

    sent = send_invite(user.email, user.full_name, raw_token, current_user.full_name)
    if sent:
        return {"message": f"Password link sent to {user.email}"}
    if not email_configured():
        return {"message": "Email is not configured yet, so the link was printed to the server log"}
    return {"message": "Could not send the email. Check the server log."}