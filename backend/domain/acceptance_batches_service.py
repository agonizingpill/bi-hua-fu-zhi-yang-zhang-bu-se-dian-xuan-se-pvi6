from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.records import get, insert, need, update
from domain.dry_swatches_model import dry_swatches
from domain.proof_regions_model import proof_regions
from domain.shade_studies_model import shade_studies
from domain.acceptance_batches_model import (
    acceptance_batches, batch_slots, batch_readings, batch_versions,
)

READING_KINDS = ('首测', '复样')


def _family(conn, family):
    row = conn.execute(
        select(dry_swatches.c.family).where(dry_swatches.c.family == family).limit(1)
    ).first()
    need(row is not None, f'色样册 {family} 没有任何干样凭证，不能开验收批')


def open_batch(conn, user, data):
    _family(conn, data.family)
    existing = conn.execute(
        select(acceptance_batches.c.code).where(acceptance_batches.c.code == data.code)
    ).first()
    need(existing is None, f'验收批 {data.code} 已存在')
    return insert(conn, acceptance_batches, code=data.code, family=data.family,
                  state='开放', current_version=0)


def add_batch_slot(conn, user, data):
    batch = get(conn, acceptance_batches, data.batch, lock=True)
    need(batch['state'] == '开放', '验收批已封批，不能再圈定试色位')
    study = get(conn, shade_studies, data.study)
    family_row = conn.execute(
        select(proof_regions.c.pigment_family)
        .where(proof_regions.c.code == study['panel']).limit(1)).first()
    need(family_row is not None and family_row[0] == batch['family'],
         '签认点所属色样册与验收批不一致')
    site_key = f"{study['panel']}:{study['x_mm']}:{study['y_mm']}"
    occupied = conn.execute(
        select(batch_slots.c.code).where(batch_slots.c.site_key == site_key)).first()
    need(occupied is None, '该试色位已圈入验收批')
    return insert(conn, batch_slots,
                  code=f"{data.batch}:{site_key}", batch=data.batch, study=data.study,
                  site_key=site_key, x_mm=study['x_mm'], y_mm=study['y_mm'],
                  # 圈点时刻锁住预测区间，之后任何签认改动都不影响本批判定。
                  lower_de=study['lower_de'], upper_de=study['upper_de'],
                  first_de=None, first_receipt=None, first_hit=None)


def _slot(conn, batch_code, site_key):
    row = conn.execute(
        select(batch_slots).where(batch_slots.c.batch == batch_code,
                                  batch_slots.c.site_key == site_key)
        .with_for_update()).mappings().first()
    need(row is not None, f'试色位 {site_key} 不在验收批 {batch_code} 内')
    return dict(row)


def _register(conn, batch_code, entries):
    """entries: list of dict(site_key, reading_kind, delta_e, receipt)。
    同一回执的重试整单幂等返回；返回新增读数条数。"""
    get(conn, acceptance_batches, batch_code, lock=True)
    need(entries, '读数清单为空')
    for entry in entries:
        need(entry['reading_kind'] in READING_KINDS, '读数类型只能是首测或复样')
        need(entry['delta_e'] >= 0, '色差读数不能为负')
    local_receipts = [entry['receipt'] for entry in entries]
    need(len(local_receipts) == len(set(local_receipts)), '同一请求内回执重复，拒绝整批提交')
    local_sites = [(entry['site_key'], entry['reading_kind']) for entry in entries]
    need(len(local_sites) == len(set(local_sites)), '同一请求内试色位读数重复，拒绝整批提交')

    known = {row['receipt'] for row in conn.execute(
        select(batch_readings.c.receipt)
        .where(batch_readings.c.receipt.in_(local_receipts))
        .with_for_update()).mappings()}
    if known:
        # 重试登记：同一回执不重复计入，也不另发一版。整单回执都已登记才视为同一重试。
        need(known == set(local_receipts), '回执编号已被其他读数占用，拒绝整批提交')
        return 0

    added = 0
    for entry in entries:
        slot = _slot(conn, batch_code, entry['site_key'])
        if entry['reading_kind'] == '首测':
            # 覆盖率只认首测：每个试色位只保留一份首测，复样永远不能顶替。
            need(slot['first_receipt'] is None,
                 f"试色位 {entry['site_key']} 已有首测记录，复样请按复样登记")
            hit = slot['lower_de'] <= entry['delta_e'] <= slot['upper_de']
            update(conn, batch_slots, slot['code'],
                   first_de=entry['delta_e'], first_receipt=entry['receipt'], first_hit=hit)
        # 保存点兜底并发下的回执唯一冲突，保证整单回滚而非污染事务。
        try:
            with conn.begin_nested():
                conn.execute(batch_readings.insert().values(
                    batch=batch_code, site_key=entry['site_key'],
                    reading_kind=entry['reading_kind'], delta_e=entry['delta_e'],
                    receipt=entry['receipt']))
        except IntegrityError:
            need(False, f"回执 {entry['receipt']} 已登记，拒绝整批提交")
        added += 1
    return added


def register_reading(conn, user, data):
    added = _register(conn, data.batch, [data.model_dump(exclude={'batch'})])
    return {'batch': data.batch, 'accepted': added,
            'note': '同回执重试未重复计入' if added == 0 else '已登记'}


def register_readings(conn, user, data):
    added = _register(conn, data.batch,
                      [entry.model_dump() for entry in data.readings])
    return {'batch': data.batch, 'accepted': added,
            'note': '同回执重试未重复计入' if added == 0 else '已登记'}


def _snapshot(conn, batch_code):
    slots = [dict(row) for row in conn.execute(
        select(batch_slots).where(batch_slots.c.batch == batch_code)
        .order_by(batch_slots.c.site_key)).mappings()]
    total = len(slots)
    measured = [slot for slot in slots if slot['first_receipt'] is not None]
    missing = [slot['site_key'] for slot in slots if slot['first_receipt'] is None]
    hits = sum(1 for slot in measured if slot['first_hit'])
    misses = len(measured) - hits
    # 覆盖率分母只含首测点；超差首测仍计入分母，不剔除、不被复样顶替。
    coverage = round(hits / len(measured), 4) if measured else None
    return {
        'total_slots': total,
        'measured_slots': len(measured),
        'missing_slots': len(missing),
        'hits': hits,
        'misses': misses,
        'coverage': coverage,
        'missing_site_keys': missing,
    }


def _publish_version(conn, batch_code, sealed_at):
    stats = _snapshot(conn, batch_code)
    batch = get(conn, acceptance_batches, batch_code, lock=True)
    next_no = batch['current_version'] + 1
    conn.execute(batch_versions.insert().values(
        batch=batch_code, version_no=next_no, sealed_at=sealed_at, **stats))
    update(conn, acceptance_batches, batch_code,
           state='已封批', current_version=next_no)
    return dict(conn.execute(
        select(batch_versions).where(
            batch_versions.c.batch == batch_code,
            batch_versions.c.version_no == next_no)).mappings().one())


def seal_batch(conn, user, data):
    batch = get(conn, acceptance_batches, data.batch, lock=True)
    need(batch['state'] == '开放', '验收批已封批，封批请改用补发版本')
    # 封批可带缺测点发布：不要求 measured_slots == total_slots。
    return _publish_version(conn, data.batch,
                            datetime.now(timezone.utc).isoformat())


def reissue_batch(conn, user, data):
    batch = get(conn, acceptance_batches, data.batch, lock=True)
    need(batch['state'] == '已封批', '验收批尚未封批，不能补发版本')
    latest = conn.execute(
        select(batch_versions).where(batch_versions.c.batch == data.batch)
        .order_by(batch_versions.c.version_no.desc()).limit(1)).mappings().one()
    stats = _snapshot(conn, data.batch)
    # 只有晚到的首测真正改变实测数时才另发一版；纯复样、同回执重试都不触发。
    need(stats['measured_slots'] != latest['measured_slots'],
         '没有新的首测记录补入，不另发版本')
    return _publish_version(conn, data.batch,
                            datetime.now(timezone.utc).isoformat())


# ---- 工作台查询 ---------------------------------------------------------

def list_batches(conn):
    return [dict(row) for row in conn.execute(
        select(acceptance_batches).order_by(acceptance_batches.c.code)).mappings()]


def list_batch_versions(conn, batch_code):
    get(conn, acceptance_batches, batch_code)
    return [dict(row) for row in conn.execute(
        select(batch_versions).where(batch_versions.c.batch == batch_code)
        .order_by(batch_versions.c.version_no)).mappings()]


def list_batch_slots(conn, batch_code):
    get(conn, acceptance_batches, batch_code)
    return [dict(row) for row in conn.execute(
        select(batch_slots).where(batch_slots.c.batch == batch_code)
        .order_by(batch_slots.c.site_key)).mappings()]


def list_batch_readings(conn, batch_code):
    get(conn, acceptance_batches, batch_code)
    return [dict(row) for row in conn.execute(
        select(batch_readings).where(batch_readings.c.batch == batch_code)
        .order_by(batch_readings.c.id)).mappings()]
