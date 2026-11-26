from fastapi import APIRouter, Depends

from core.auth import actor, allow
from core.audit import record
from core.db import transaction
from domain import acceptance_batches_service as service
from domain.acceptance_batches_schemas import (
    AddBatchSlot, BatchRef, OpenBatch, RegisterReading, RegisterReadings,
)

router = APIRouter()


@router.get('/acceptance_batches')
def list_batches(user=Depends(actor)):
    with transaction() as conn:
        return service.list_batches(conn)


@router.get('/acceptance_batches/versions')
def list_versions(batch: str, user=Depends(actor)):
    with transaction() as conn:
        return service.list_batch_versions(conn, batch)


@router.get('/acceptance_batches/slots')
def list_slots(batch: str, user=Depends(actor)):
    with transaction() as conn:
        return service.list_batch_slots(conn, batch)


@router.get('/acceptance_batches/readings')
def list_readings(batch: str, user=Depends(actor)):
    with transaction() as conn:
        return service.list_batch_readings(conn, batch)


@router.post('/acceptance_batches/open-batch')
def open_batch(data: OpenBatch, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        output = service.open_batch(conn, user, data)
        record(conn, user, 'acceptance_batches.open_batch')
        return output


@router.post('/acceptance_batches/add-slot')
def add_slot(data: AddBatchSlot, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        output = service.add_batch_slot(conn, user, data)
        record(conn, user, 'acceptance_batches.add_slot')
        return output


@router.post('/acceptance_batches/register-reading')
def register_reading(data: RegisterReading, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        output = service.register_reading(conn, user, data)
        record(conn, user, 'acceptance_batches.register_reading')
        return output


@router.post('/acceptance_batches/register-readings')
def register_readings(data: RegisterReadings, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        output = service.register_readings(conn, user, data)
        record(conn, user, 'acceptance_batches.register_readings')
        return output


@router.post('/acceptance_batches/seal-batch')
def seal_batch(data: BatchRef, user=Depends(actor)):
    allow(user, ('reviewer',))
    with transaction() as conn:
        output = service.seal_batch(conn, user, data)
        record(conn, user, 'acceptance_batches.seal_batch')
        return output


@router.post('/acceptance_batches/reissue-batch')
def reissue_batch(data: BatchRef, user=Depends(actor)):
    allow(user, ('reviewer',))
    with transaction() as conn:
        output = service.reissue_batch(conn, user, data)
        record(conn, user, 'acceptance_batches.reissue_batch')
        return output
