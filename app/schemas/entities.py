from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def trim_required_string(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


def trim_optional_string(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def require_timezone_and_normalize_utc(value: Any) -> Any:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value

    if isinstance(parsed, datetime):
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("datetime must include timezone information")
        return parsed.astimezone(UTC)
    return value


def normalize_output_datetime_utc(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return value


class LocationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    address: str | None = Field(default=None, max_length=240)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        return trim_required_string(value)

    @field_validator("address", mode="before")
    @classmethod
    def trim_address(cls, value: Any) -> Any:
        return trim_optional_string(value)


class LocationUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    address: str | None = Field(default=None, max_length=240)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        return trim_required_string(value)

    @field_validator("address", mode="before")
    @classmethod
    def trim_address(cls, value: Any) -> Any:
        return trim_optional_string(value)


class LocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    address: str | None
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: Any) -> Any:
        return normalize_output_datetime_utc(value)


class RoomCreate(BaseModel):
    location_id: str
    name: str = Field(min_length=2, max_length=120)
    capacity: int = Field(default=1, ge=1, le=500)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        return trim_required_string(value)


class RoomUpdate(BaseModel):
    location_id: str
    name: str = Field(min_length=2, max_length=120)
    capacity: int = Field(default=1, ge=1, le=500)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        return trim_required_string(value)


class RoomRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    location_id: str
    name: str
    capacity: int
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: Any) -> Any:
        return normalize_output_datetime_utc(value)


class ReservationInputBase(BaseModel):
    location_id: str
    room_id: str
    start_at: datetime
    end_at: datetime
    responsible: str = Field(min_length=2, max_length=160)
    coffee: bool = False
    attendees: int | None = Field(default=None, ge=1, le=500)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def normalize_datetime(cls, value: Any) -> Any:
        return require_timezone_and_normalize_utc(value)

    @field_validator("responsible", mode="before")
    @classmethod
    def trim_responsible(cls, value: Any) -> Any:
        return trim_required_string(value)

    @field_validator("description", mode="before")
    @classmethod
    def trim_description(cls, value: Any) -> Any:
        return trim_optional_string(value)

    @model_validator(mode="after")
    def validate_business_rules(self) -> Self:
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        if self.coffee and self.attendees is None:
            raise ValueError("attendees is required when coffee is true")
        if not self.coffee:
            self.attendees = None
        return self


class ReservationCreate(ReservationInputBase):
    pass


class ReservationUpdate(ReservationInputBase):
    pass


class ReservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    location_id: str
    room_id: str
    start_at: datetime
    end_at: datetime
    responsible: str
    coffee: bool
    attendees: int | None
    description: str | None
    created_by_user_id: str
    created_by_email: str
    created_at: datetime
    updated_at: datetime
    location_name: str
    room_name: str

    @field_validator("start_at", "end_at", "created_at", "updated_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: Any) -> Any:
        return normalize_output_datetime_utc(value)


class BulkDeleteRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


class BulkDeleteResponse(BaseModel):
    deleted: int
