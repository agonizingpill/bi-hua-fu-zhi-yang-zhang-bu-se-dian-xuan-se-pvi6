"""验收批领域规则测试：SQLite 内存库 + 直接调用服务层。
运行：/tmp/venv/bin/python -I backend/tests/test_acceptance_batches.py"""
import os
import sys
import tempfile
import unittest

_DB_FD, _DB_PATH = tempfile.mkstemp(prefix='acceptance-test-', suffix='.db')
os.close(_DB_FD)
os.unlink(_DB_PATH)
os.environ.setdefault('DATABASE_URL', f'sqlite:///{_DB_PATH}')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
os.environ.setdefault('JWT_SECRET', 'test-secret')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException
from sqlalchemy import select

from core.db import engine, metadata
from core.records import insert
from domain.proof_regions_model import proof_regions
from domain.dry_swatches_model import dry_swatches
from domain.shade_studies_model import shade_studies
from domain.acceptance_batches_model import (
    acceptance_batches, batch_slots, batch_readings, batch_versions,
)
from domain import acceptance_batches_service as svc
from domain.acceptance_batches_schemas import (
    AddBatchSlot, BatchRef, OpenBatch, ReadingEntry, RegisterReading, RegisterReadings,
)

REVIEWER = {'sub': 'reviewer@demo.test', 'role': 'reviewer'}
OPERATOR = {'sub': 'operator@demo.test', 'role': 'operator'}


def expect_409(fn, needle):
    try:
        fn()
    except HTTPException as exc:
        assert exc.status_code == 409, exc.status_code
        assert needle in exc.detail, exc.detail
        return
    raise AssertionError(f'应拒绝 409（{needle}），但调用成功')


class AcceptanceBatchTests(unittest.TestCase):
    def setUp(self):
        metadata.drop_all(engine)
        metadata.create_all(engine)
        with engine.begin() as conn:
            insert(conn, proof_regions, code='PANEL-A', label='样张A',
                   pigment_family='SAND', width_mm=100, height_mm=100, frame_inset=2,
                   hold_x1=10, hold_y1=10, hold_x2=20, hold_y2=20, revision=1)
            insert(conn, dry_swatches, code='SW-1', family='SAND', fraction=.5,
                   ground_l=58.0, delta_e=2.0, sample_ref='REF-1', dry_hours=28,
                   witnessed=True)
            # 两个签认点，预测区间分别 [1.0, 2.0] 与 [3.0, 4.0]
            insert(conn, shade_studies, code='STUDY-1', panel='PANEL-A', revision=1,
                   x_mm=30, y_mm=30, ground_l=58.0, fraction=.5, mean_de=1.5,
                   lower_de=1.0, upper_de=2.0, test_mae=.1, token='TOKEN-1')
            insert(conn, shade_studies, code='STUDY-2', panel='PANEL-A', revision=1,
                   x_mm=40, y_mm=40, ground_l=58.0, fraction=.5, mean_de=3.5,
                   lower_de=3.0, upper_de=4.0, test_mae=.1, token='TOKEN-2')
            # 另一色样册的签认点
            insert(conn, proof_regions, code='PANEL-B', label='样张B',
                   pigment_family='OCHRE', width_mm=100, height_mm=100, frame_inset=2,
                   hold_x1=10, hold_y1=10, hold_x2=20, hold_y2=20, revision=1)
            insert(conn, dry_swatches, code='SW-2', family='OCHRE', fraction=.5,
                   ground_l=58.0, delta_e=2.0, sample_ref='REF-2', dry_hours=28,
                   witnessed=True)
            insert(conn, shade_studies, code='STUDY-3', panel='PANEL-B', revision=1,
                   x_mm=30, y_mm=30, ground_l=58.0, fraction=.5, mean_de=3.5,
                   lower_de=3.0, upper_de=4.0, test_mae=.1, token='TOKEN-3')

    def tx(self):
        return engine.begin()

    # 规则 1：按色样册开批
    def test_open_batch_requires_family(self):
        with self.tx() as conn:
            expect_409(lambda: svc.open_batch(conn, REVIEWER,
                          OpenBatch(code='B1', family='GHOST')), '干样凭证')

    def test_open_batch_and_family_mismatch_rejected(self):
        with self.tx() as conn:
            svc.open_batch(conn, REVIEWER, OpenBatch(code='B1', family='SAND'))
            expect_409(lambda: svc.add_batch_slot(conn, OPERATOR,
                              AddBatchSlot(batch='B1', study='STUDY-3')), '色样册')
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-1'))
            # 重复圈点被拒绝
            expect_409(lambda: svc.add_batch_slot(conn, OPERATOR,
                              AddBatchSlot(batch='B1', study='STUDY-1')), '已圈入')

    # 规则 2/3：锁区间；覆盖率只算首测，超差也算实测
    def test_coverage_only_counts_first_readings_and_misses_count(self):
        with self.tx() as conn:
            svc.open_batch(conn, REVIEWER, OpenBatch(code='B1', family='SAND'))
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-1'))
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-2'))
            # 点1 首测超差（2.5 > 2.0），随后复样落区间 1.5 —— 复样不得抬覆盖率
            svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='首测',
                delta_e=2.5, receipt='R-1'))
            svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='复样',
                delta_e=1.5, receipt='R-2'))
            # 点2 首测命中
            svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:40:40', reading_kind='首测',
                delta_e=3.5, receipt='R-3'))
            stats = svc._snapshot(conn, 'B1')
            assert stats['total_slots'] == 2
            assert stats['measured_slots'] == 2
            assert stats['hits'] == 1 and stats['misses'] == 1
            assert stats['coverage'] == 0.5, stats['coverage']

    # 规则：先交复样、首测仍缺 → 仍算缺测
    def test_retest_before_first_reading_still_missing(self):
        with self.tx() as conn:
            svc.open_batch(conn, REVIEWER, OpenBatch(code='B1', family='SAND'))
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-1'))
            svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='复样',
                delta_e=1.5, receipt='R-9'))
            stats = svc._snapshot(conn, 'B1')
            assert stats['measured_slots'] == 0
            assert stats['missing_slots'] == 1
            assert stats['coverage'] is None
            assert stats['missing_site_keys'] == ['PANEL-A:30:30']
            # 之后补首测，仍按首测判定（超差）
            svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='首测',
                delta_e=2.5, receipt='R-10'))
            stats = svc._snapshot(conn, 'B1')
            assert stats['measured_slots'] == 1 and stats['hits'] == 0
            assert stats['coverage'] == 0.0

    # 规则：封批可带缺测发布
    def test_seal_allows_missing_slots(self):
        with self.tx() as conn:
            svc.open_batch(conn, REVIEWER, OpenBatch(code='B1', family='SAND'))
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-1'))
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-2'))
            svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='首测',
                delta_e=1.5, receipt='R-1'))
            v1 = svc.seal_batch(conn, REVIEWER, BatchRef(batch='B1'))
            assert v1['version_no'] == 1
            assert v1['measured_slots'] == 1 and v1['missing_slots'] == 1
            assert v1['coverage'] == 1.0
            # 封批后不能再圈点
            expect_409(lambda: svc.add_batch_slot(conn, OPERATOR,
                              AddBatchSlot(batch='B1', study='STUDY-3')), '封批')

    # 规则：晚到首测补入原批，另发一版；旧版不变；纯复样不发版
    def test_late_first_reading_reissues_but_retest_does_not(self):
        with self.tx() as conn:
            svc.open_batch(conn, REVIEWER, OpenBatch(code='B1', family='SAND'))
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-1'))
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-2'))
            svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='首测',
                delta_e=1.5, receipt='R-1'))
            v1 = svc.seal_batch(conn, REVIEWER, BatchRef(batch='B1'))

            # 封批后到达的纯复样：不另发一版
            svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='复样',
                delta_e=1.6, receipt='R-2'))
            expect_409(lambda: svc.reissue_batch(conn, REVIEWER,
                              BatchRef(batch='B1')), '不另发版本')

            # 晚到的首测（原缺测点）→ 另发一版
            svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:40:40', reading_kind='首测',
                delta_e=9.9, receipt='R-3'))  # 超差也算实测
            v2 = svc.reissue_batch(conn, REVIEWER, BatchRef(batch='B1'))
            assert v2['version_no'] == 2
            assert v2['measured_slots'] == 2 and v2['missing_slots'] == 0
            assert v2['hits'] == 1 and v2['misses'] == 1
            assert v2['coverage'] == 0.5

            # 旧版快照原样保留
            old = conn.execute(select(batch_versions).where(
                batch_versions.c.version_no == 1)).mappings().one()
            assert old['measured_slots'] == 1 and old['coverage'] == 1.0
            assert old['missing_site_keys'] == ['PANEL-A:40:40']

            # 没有新首测时再次补发被拒绝，不产生 v3
            expect_409(lambda: svc.reissue_batch(conn, REVIEWER,
                              BatchRef(batch='B1')), '不另发版本')
            versions = svc.list_batch_versions(conn, 'B1')
            assert [v['version_no'] for v in versions] == [1, 2]

    # 规则：两点同时提交都算进去（批量接口，单事务）
    def test_bulk_submission_counts_all_points(self):
        with self.tx() as conn:
            svc.open_batch(conn, REVIEWER, OpenBatch(code='B1', family='SAND'))
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-1'))
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-2'))
            out = svc.register_readings(conn, OPERATOR, RegisterReadings(batch='B1', readings=[
                ReadingEntry(site_key='PANEL-A:30:30', reading_kind='首测',
                             delta_e=1.5, receipt='B-1'),
                ReadingEntry(site_key='PANEL-A:40:40', reading_kind='首测',
                             delta_e=3.5, receipt='B-2'),
            ]))
            assert out['accepted'] == 2
            stats = svc._snapshot(conn, 'B1')
            assert stats['measured_slots'] == 2 and stats['hits'] == 2

            # 同一批回执整体重试 → 幂等，不重复计入
            again = svc.register_readings(conn, OPERATOR, RegisterReadings(batch='B1', readings=[
                ReadingEntry(site_key='PANEL-A:30:30', reading_kind='首测',
                             delta_e=1.5, receipt='B-1'),
                ReadingEntry(site_key='PANEL-A:40:40', reading_kind='首测',
                             delta_e=3.5, receipt='B-2'),
            ]))
            assert again['accepted'] == 0
            assert conn.execute(select(batch_readings)).mappings().all().__len__() == 2

    # 规则：同一回执不重复计入（单条重试）
    def test_duplicate_receipt_is_idempotent(self):
        with self.tx() as conn:
            svc.open_batch(conn, REVIEWER, OpenBatch(code='B1', family='SAND'))
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-1'))
            svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='首测',
                delta_e=1.5, receipt='DUP-1'))
            again = svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='首测',
                delta_e=1.5, receipt='DUP-1'))
            assert again['accepted'] == 0
            rows = conn.execute(select(batch_readings)).mappings().all()
            assert len(rows) == 1
            # 已首测的点不能再补第二份首测
            expect_409(lambda: svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='首测',
                delta_e=1.6, receipt='OTHER-1')), '已有首测')

    # 服务层入参校验：负色差、非法读数类型、批外试色位
    def test_invalid_reading_inputs_rejected(self):
        with self.tx() as conn:
            svc.open_batch(conn, REVIEWER, OpenBatch(code='B1', family='SAND'))
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-1'))
            expect_409(lambda: svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='首测',
                delta_e=-0.1, receipt='NEG-1')), '不能为负')
            expect_409(lambda: svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='目测',
                delta_e=1.0, receipt='KIND-1')), '首测或复样')
            expect_409(lambda: svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:99:99', reading_kind='首测',
                delta_e=1.0, receipt='OUT-1')), '不在验收批')

    # 工作台：每版实测数、覆盖率、缺测点可查
    def test_workbench_queries(self):
        with self.tx() as conn:
            svc.open_batch(conn, REVIEWER, OpenBatch(code='B1', family='SAND'))
            svc.add_batch_slot(conn, OPERATOR, AddBatchSlot(batch='B1', study='STUDY-1'))
            svc.register_reading(conn, OPERATOR, RegisterReading(
                batch='B1', site_key='PANEL-A:30:30', reading_kind='复样',
                delta_e=1.4, receipt='W-1'))
            svc.seal_batch(conn, REVIEWER, BatchRef(batch='B1'))
            batches = svc.list_batches(conn)
            assert batches[0]['current_version'] == 1
            versions = svc.list_batch_versions(conn, 'B1')
            assert versions[0]['missing_site_keys'] == ['PANEL-A:30:30']
            slots = svc.list_batch_slots(conn, 'B1')
            assert slots[0]['first_de'] is None  # 复样不写首测列
            readings = svc.list_batch_readings(conn, 'B1')
            assert readings[0]['reading_kind'] == '复样'


if __name__ == '__main__':
    unittest.main(verbosity=2)
