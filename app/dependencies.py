from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models
from app.security import decode_access_token


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user_id = decode_access_token(token)
    if user_id is None:
        raise credentials_exception
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise credentials_exception
    if not user.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated")
    return user


def get_membership(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    x_clinic_id: int | None = Header(default=None),
) -> models.ClinicMembership:
    """Resolve which clinic the user is acting in for this request.
    If a clinic is specified via header, the user must be a member of it.
    Otherwise fall back to their default clinic_id."""
    target_clinic = x_clinic_id if x_clinic_id is not None else current_user.clinic_id

    membership = db.query(models.ClinicMembership).filter(
        models.ClinicMembership.user_id == current_user.id,
        models.ClinicMembership.clinic_id == target_clinic,
    ).first()

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this clinic",
        )
    return membership


def get_clinic_id(membership: models.ClinicMembership = Depends(get_membership)) -> int:
    return membership.clinic_id


def require_roles(*allowed_roles: str):
    """Role check against the membership role in the currently selected clinic."""
    def guard(membership: models.ClinicMembership = Depends(get_membership)) -> models.ClinicMembership:
        if membership.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to do this",
            )
        return membership
    return guard


require_admin = require_roles("owner", "admin")
require_owner = require_roles("owner")