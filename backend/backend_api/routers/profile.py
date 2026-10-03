from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from ecommerce_common.config import Settings
from ecommerce_common.errors import AuthenticationError
from ecommerce_common.log import log_event
from ecommerce_common.models import User, UserProfile
from ecommerce_common.security import hash_password, verify_password
from ecommerce_common.storage import StorageService, StorageUnavailableError, avatar_key

from ..deps import get_current_user, get_db, get_settings, get_storage
from ..schemas import Message, PasswordChange, ProfileUpdate, UserOut
from ..services.images import process_image, read_upload
from ..services.serializers import user_out

router = APIRouter(prefix="/api/profile", tags=["Profile"])
logger = logging.getLogger("ecommerce.profile")


@router.get("", response_model=UserOut, summary="Get my profile")
def get_profile(user: User = Depends(get_current_user), storage: StorageService = Depends(get_storage)) -> UserOut:
    return user_out(user, storage)


@router.put("", response_model=UserOut, summary="Update my profile (only provided fields change)")
def update_profile(
    body: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: StorageService = Depends(get_storage),
) -> UserOut:
    user = db.merge(user)
    data = body.model_dump(exclude_unset=True)
    if "full_name" in data and data["full_name"]:
        user.full_name = data.pop("full_name")
    data.pop("full_name", None)
    if user.profile is None:
        user.profile = UserProfile()
    for field, value in data.items():
        setattr(user.profile, field, value)
    db.commit()
    db.refresh(user)
    log_event("PROFILE_UPDATED", user_id=user.id, fields=sorted(body.model_fields_set))
    return user_out(user, storage)


@router.put("/password", response_model=Message, summary="Change my password")
def change_password(
    body: PasswordChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Message:
    user = db.merge(user)
    if not verify_password(body.current_password, user.password_hash):
        raise AuthenticationError("Current password is incorrect", code="INVALID_CREDENTIALS")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    log_event("PASSWORD_CHANGED", user_id=user.id)
    return Message(message="Password updated")


@router.post("/avatar", response_model=UserOut, summary="Upload my avatar (stored in OBS images/avatars/)")
def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    raw = read_upload(file, settings.max_upload_mb * 1024 * 1024)
    data, content_type, filename = process_image(raw, max_side=512)
    key = avatar_key(user.id, filename)
    storage.upload(key, data, content_type)
    user = db.merge(user)
    if user.profile is None:
        user.profile = UserProfile()
    old_key = user.profile.avatar_key
    user.profile.avatar_key = key
    db.commit()
    db.refresh(user)
    if old_key:
        try:
            storage.delete(old_key)
        except StorageUnavailableError:
            logger.warning("could not delete old avatar", extra={"object_key": old_key})
    log_event("AVATAR_UPDATED", user_id=user.id, object_key=key)
    return user_out(user, storage)
