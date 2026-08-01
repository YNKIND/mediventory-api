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


def get_clinic_user(db: Session, user_id: int, clinic_id: int) -> models.User:
    user = db.query(models.User).filter(
        models.User.id == user_id,
        models.User.clinic_id == clinic_id,
    ).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("", response_model=list[schemas.UserOut])
def list_users(
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(require_admin),
):
    return db.query(models.User).filter(
        models.User.clinic_id == clinic_id
    ).order_by(models.User.full_name).all()


@router.post("", response_model=schemas.UserOut)
def create_user(
    payload: schemas.UserAdminCreate,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(require_admin),
):
    assert_role_valid(payload.role)
    if payload.role == "owner" and current_user.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an owner can create another owner")

    email = payload.email.lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An account with that email already exists")
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

    if payload.send_invite:
        raw_token, token_hash = generate_reset_token()
        db.add(models.PasswordResetToken(user_id=user.id, token_hash=token_hash, expires_at=reset_token_expiry()))
        db.commit()
        db.refresh(user)
        send_invite(user.email, user.full_name, raw_token, current_user.full_name)
    else:
        db.commit()
        db.refresh(user)

    return user


@router.patch("/{user_id}", response_model=schemas.UserOut)
def update_user(
    user_id: int,
    payload: schemas.UserAdminUpdate,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(require_admin),
):
    user = get_clinic_user(db, user_id, clinic_id)

    if payload.full_name is not None:
        if not payload.full_name.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Full name cannot be empty")
        user.full_name = payload.full_name.strip()

    if payload.role is not None and payload.role != user.role:
        assert_role_valid(payload.role)
        if current_user.role != "owner":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an owner can change roles")
        if user.id == current_user.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot change your own role")
        if user.role == "owner":
            remaining = db.query(models.User).filter(
                models.User.clinic_id == clinic_id,
                models.User.role == "owner",
                models.User.active == True,
                models.User.id != user.id,
            ).count()
            if remaining == 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="There must always be at least one active owner")
        user.role = payload.role

    if payload.active is not None and payload.active != user.active:
        if user.id == current_user.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot deactivate your own account")
        if not payload.active and user.role == "owner":
            remaining = db.query(models.User).filter(
                models.User.clinic_id == clinic_id,
                models.User.role == "owner",
                models.User.active == True,
                models.User.id != user.id,
            ).count()
            if remaining == 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="There must always be at least one active owner")
        user.active = payload.active

    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/send-reset", response_model=schemas.SimpleMessage)
def send_reset_for_user(
    user_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(require_admin),
):
    user = get_clinic_user(db, user_id, clinic_id)
    if not user.active:
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