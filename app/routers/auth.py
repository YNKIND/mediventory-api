from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app import models, schemas
from app.dependencies import get_db, get_current_user, get_membership
from app.email import send_password_reset
from app.security import (
    hash_password, verify_password, create_access_token,
    generate_reset_token, hash_reset_token, reset_token_expiry, validate_password,
)


router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC_RESET_MESSAGE = "If that email has an account, a reset link is on its way."


@router.post("/register", response_model=schemas.UserOut)
def register(payload: schemas.RegisterRequest, db: Session = Depends(get_db)):
    """Creates a new clinic and its first owner account together."""
    password_error = validate_password(payload.password)
    if password_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=password_error)

    email = payload.email.lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An account with that email already exists")
    if not payload.clinic_name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Clinic name is required")

    clinic = models.Clinic(name=payload.clinic_name.strip())
    db.add(clinic)
    db.flush()

    user = models.User(
        clinic_id=clinic.id,
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        role="owner",
        active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=schemas.Token)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email.lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated")
    return {"access_token": create_access_token(user.id), "token_type": "bearer"}


@router.post("/token", response_model=schemas.Token)
def login_form(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == form_data.username.lower()).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated")
    return {"access_token": create_access_token(user.id), "token_type": "bearer"}


@router.get("/me", response_model=schemas.MeOut)
def read_me(
    membership: models.ClinicMembership = Depends(get_membership),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    clinic = db.query(models.Clinic).filter(models.Clinic.id == membership.clinic_id).first()
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": membership.role,  # role in the currently selected clinic
        "active": current_user.active,
        "clinic_id": membership.clinic_id,
        "clinic_name": clinic.name if clinic else "",
    }


@router.post("/change-password", response_model=schemas.SimpleMessage)
def change_password(
    payload: schemas.ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    password_error = validate_password(payload.new_password)
    if password_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=password_error)
    current_user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"message": "Password updated"}


@router.post("/forgot-password", response_model=schemas.SimpleMessage)
def forgot_password(payload: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email.lower()).first()
    if not user or not user.active:
        return {"message": GENERIC_RESET_MESSAGE}

    raw_token, token_hash = generate_reset_token()
    db.add(models.PasswordResetToken(user_id=user.id, token_hash=token_hash, expires_at=reset_token_expiry()))
    db.commit()
    send_password_reset(user.email, user.full_name, raw_token)
    return {"message": GENERIC_RESET_MESSAGE}


@router.post("/reset-password", response_model=schemas.SimpleMessage)
def reset_password(payload: schemas.ResetPasswordRequest, db: Session = Depends(get_db)):
    password_error = validate_password(payload.new_password)
    if password_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=password_error)

    token_hash = hash_reset_token(payload.token)
    record = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.token_hash == token_hash
    ).first()

    invalid = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This reset link is invalid or has expired. Request a new one.")
    if not record or record.used_at is not None:
        raise invalid
    if record.expires_at < datetime.now(timezone.utc):
        raise invalid

    user = db.query(models.User).filter(models.User.id == record.user_id).first()
    if not user or not user.active:
        raise invalid

    user.password_hash = hash_password(payload.new_password)
    record.used_at = datetime.now(timezone.utc)

    others = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.user_id == user.id,
        models.PasswordResetToken.used_at.is_(None),
    ).all()
    for other in others:
        other.used_at = datetime.now(timezone.utc)

    db.commit()
    return {"message": "Password updated. You can sign in now."}

@router.get("/my-clinics", response_model=list[schemas.ClinicMembershipOut])
def my_clinics(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    memberships = db.query(models.ClinicMembership).filter(
        models.ClinicMembership.user_id == current_user.id
    ).all()
    result = []
    for m in memberships:
        clinic = db.query(models.Clinic).filter(models.Clinic.id == m.clinic_id).first()
        if clinic:
            result.append({
                "clinic_id": clinic.id,
                "clinic_name": clinic.name,
                "role": m.role,
            })
    result.sort(key=lambda r: r["clinic_name"].lower())
    return result
@router.post("/create-clinic", response_model=schemas.ClinicMembershipOut)
def create_clinic(
    payload: schemas.CreateClinicRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not payload.clinic_name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Clinic name is required")

    clinic = models.Clinic(name=payload.clinic_name.strip())
    db.add(clinic)
    db.flush()

    db.add(models.ClinicMembership(user_id=current_user.id, clinic_id=clinic.id, role="owner"))
    db.commit()

    return {"clinic_id": clinic.id, "clinic_name": clinic.name, "role": "owner"}