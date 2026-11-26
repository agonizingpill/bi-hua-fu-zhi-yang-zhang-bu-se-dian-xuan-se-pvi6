from typing import Annotated, List

from pydantic import BaseModel, ConfigDict, FiniteFloat, StringConstraints


class OpenBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    family: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class AddBatchSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    study: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class RegisterReading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    site_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    reading_kind: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    delta_e: FiniteFloat
    receipt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class ReadingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    reading_kind: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    delta_e: FiniteFloat
    receipt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class RegisterReadings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    readings: Annotated[List[ReadingEntry], ...]


class BatchRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
