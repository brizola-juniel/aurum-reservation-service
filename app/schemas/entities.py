from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LocationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    address: str | None = Field(default=None, max_length=240)


class LocationUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    address: str | None = Field(default=None, max_length=240)


class LocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    address: str | None
    created_at: datetime


class RoomCreate(BaseModel):
    location_id: str
    name: str = Field(min_length=2, max_length=120)
    capacity: int = Field(default=1, ge=1, le=500)


class RoomUpdate(BaseModel):
    location_id: str
    name: str = Field(min_length=2, max_length=120)
    capacity: int = Field(default=1, ge=1, le=500)


class RoomRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    location_id: str
    name: str
    capacity: int
    created_at: datetime


class ReservationBase(BaseModel):
    location_id: str
    room_id: str
    start_at: datetime
    end_at: datetime
    responsible: str = Field(min_length=2, max_length=160)
    coffee: bool = False
    attendees: int | None = Field(default=None, ge=1, le=500)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_business_rules(self) -> "ReservationBase":
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        if self.coffee and self.attendees is None:
            raise ValueError("attendees is required when coffee is true")
        if not self.coffee:
            self.attendees = None
        return self


class ReservationCreate(ReservationBase):
    pass


class ReservationUpdate(ReservationBase):
    pass


class ReservationRead(ReservationBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_by_user_id: str
    created_by_email: str
    created_at: datetime
    updated_at: datetime
    location_name: str
    room_name: str


class BulkDeleteRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


class BulkDeleteResponse(BaseModel):
    deleted: int
