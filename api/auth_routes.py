"""Authentication endpoints: login and current-user."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import create_access_token, verify_password, get_current_user
from backend.database import get_db
from backend.models import User
from backend.schemas import LoginResponse, UserResponse

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Authenticate with employee number and password; returns JWT."""
    result = await db.execute(select(User).where(User.employee_number == form.username))
    user: User | None = result.scalar_one_or_none()

    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect employee number or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    # Update last login timestamp
    user.last_login = datetime.utcnow()
    await db.commit()

    token = create_access_token({"sub": user.employee_number, "role": user.role.value})
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        role=user.role.value,
        full_name=user.full_name,
        employee_number=user.employee_number,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Return current user profile."""
    return current_user
