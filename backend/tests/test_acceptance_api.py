"""验收批 API 端到端测试：FastAPI TestClient + SQLite。
运行：/tmp/venv/bin/python -I backend/tests/test_acceptance_api.py"""
import os
import sys
import tempfile
import unittest

_DB_FD, _DB_PATH = tempfile.mkstemp(prefix='acceptance-api-', suffix='.db')
os.close(_DB_FD)
os.unlink(_DB_PATH)
os.environ.setdefault('DATABASE_URL', f'sqlite:///{_DB_PATH}')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ['SEED_DEMO'] = 'false'

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from core.app import app
from core.db import engine, metadata
from core.records import insert
from domain.proof_regions_model import proof_regions
from domain.dry_swatches_model import dry_swatches
from domain.shade_studies_model import shade_studies


class AcceptanceApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        # 每个用例重建表并灌入固定基础数据，保证彼此隔离。
        metadata.drop_all(engine)
        metadata.create_all(engine)
        with engine.begin() as conn:
            insert(conn, proof_regions, code='PANEL-A', label='样张A',
                   pigment_family='SAND', width_mm=100, height_mm=100, frame_inset=2,
                   hold_x1=10, hold_y1=10, hold_x2=20, hold_y2=20, revision=1)
            insert(conn, dry_swatches, code='SW-1', family='SAND', fraction=.5,
                   ground_l=58.0, delta_e=2.0, sample_ref='REF-1', dry_hours=28,
                   witnessed=True)
            insert(conn, shade_studies, code='STUDY-1', panel='PANEL-A', revision=1,
                   x_mm=30, y_mm=30, ground_l=58.0, fraction=.5, mean_de=1.5,
                   lower_de=1.0, upper_de=2.0, test_mae=.1, token='TOKEN-1')
            insert(conn, shade_studies, code='STUDY-2', panel='PANEL-A', revision=1,
                   x_mm=40, y_mm=40, ground_l=58.0, fraction=.5, mean_de=3.5,
                   lower_de=3.0, upper_de=4.0, test_mae=.1, token='TOKEN-2')

    def token(self, role):
        resp = self.client.post('/auth/login', json={
            'email': f'{role}@demo.test', 'password': 'DemoC0de!2026'})
        self.assertEqual(resp.status_code, 200)
        return {'Authorization': 'Bearer ' + resp.json()['token']}

    def test_full_lifecycle_and_permissions(self):
        reviewer = self.token('reviewer')
        operator = self.token('operator')
        viewer = self.token('viewer')
        c = self.client

        # viewer 不能开批
        self.assertEqual(c.post('/acceptance_batches/open-batch',
                                json={'code': 'B1', 'family': 'SAND'},
                                headers=viewer).status_code, 403)
        # 未登录 401
        self.assertEqual(c.post('/acceptance_batches/open-batch',
                                json={'code': 'B1', 'family': 'SAND'}).status_code, 401)

        r = c.post('/acceptance_batches/open-batch',
                   json={'code': 'B1', 'family': 'SAND'}, headers=operator)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['state'], '开放')

        for study in ('STUDY-1', 'STUDY-2'):
            r = c.post('/acceptance_batches/add-slot',
                       json={'batch': 'B1', 'study': study}, headers=operator)
            self.assertEqual(r.status_code, 200, r.text)

        # 锁定的区间随试色位返回
        slots = c.get('/acceptance_batches/slots?batch=B1', headers=viewer).json()
        self.assertEqual([s['lower_de'] for s in slots], [1.0, 3.0])

        # 两点同时提交：一个命中、一个超差
        r = c.post('/acceptance_batches/register-readings', headers=operator, json={
            'batch': 'B1', 'readings': [
                {'site_key': 'PANEL-A:30:30', 'reading_kind': '首测',
                 'delta_e': 1.5, 'receipt': 'R-1'},
                {'site_key': 'PANEL-A:40:40', 'reading_kind': '首测',
                 'delta_e': 9.9, 'receipt': 'R-2'},
            ]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['accepted'], 2)

        # 同回执重试：幂等，accepted=0
        r = c.post('/acceptance_batches/register-readings', headers=operator, json={
            'batch': 'B1', 'readings': [
                {'site_key': 'PANEL-A:30:30', 'reading_kind': '首测',
                 'delta_e': 1.5, 'receipt': 'R-1'},
                {'site_key': 'PANEL-A:40:40', 'reading_kind': '首测',
                 'delta_e': 9.9, 'receipt': 'R-2'},
            ]})
        self.assertEqual(r.json()['accepted'], 0)

        # 封批（operator 无权，reviewer 才行）
        self.assertEqual(c.post('/acceptance_batches/seal-batch',
                                json={'batch': 'B1'}, headers=operator).status_code, 403)
        v1 = c.post('/acceptance_batches/seal-batch',
                    json={'batch': 'B1'}, headers=reviewer).json()
        self.assertEqual(v1['version_no'], 1)
        self.assertEqual(v1['measured_slots'], 2)
        self.assertEqual(v1['hits'], 1)
        self.assertEqual(v1['misses'], 1)
        self.assertEqual(v1['coverage'], 0.5)

        # 封批后圈点被拒
        r = c.post('/acceptance_batches/add-slot',
                   json={'batch': 'B1', 'study': 'STUDY-1'}, headers=operator)
        self.assertEqual(r.status_code, 409)

        # 纯复样到达，补发被拒、不另发版
        r = c.post('/acceptance_batches/register-reading', headers=operator, json={
            'batch': 'B1', 'site_key': 'PANEL-A:30:30', 'reading_kind': '复样',
            'delta_e': 1.6, 'receipt': 'R-3'})
        self.assertEqual(r.status_code, 200, r.text)
        r = c.post('/acceptance_batches/reissue-batch',
                   json={'batch': 'B1'}, headers=reviewer)
        self.assertEqual(r.status_code, 409)

        # 工作台：版本台账、读数台账
        versions = c.get('/acceptance_batches/versions?batch=B1',
                         headers=viewer).json()
        self.assertEqual(len(versions), 1)
        readings = c.get('/acceptance_batches/readings?batch=B1',
                         headers=viewer).json()
        self.assertEqual([x['receipt'] for x in readings], ['R-1', 'R-2', 'R-3'])

    def test_missing_slots_release_and_late_first_reading(self):
        reviewer = self.token('reviewer')
        operator = self.token('operator')
        viewer = self.token('viewer')
        c = self.client
        c.post('/acceptance_batches/open-batch',
               json={'code': 'B2', 'family': 'SAND'}, headers=operator)
        c.post('/acceptance_batches/add-slot',
               json={'batch': 'B2', 'study': 'STUDY-1'}, headers=operator)
        c.post('/acceptance_batches/add-slot',
               json={'batch': 'B2', 'study': 'STUDY-2'}, headers=operator)
        # 只交一个复样：该点仍算缺测，封批带缺测发布
        c.post('/acceptance_batches/register-reading', headers=operator, json={
            'batch': 'B2', 'site_key': 'PANEL-A:30:30', 'reading_kind': '复样',
            'delta_e': 1.4, 'receipt': 'M-1'})
        v1 = c.post('/acceptance_batches/seal-batch',
                    json={'batch': 'B2'}, headers=reviewer).json()
        self.assertEqual(v1['measured_slots'], 0)
        self.assertEqual(v1['missing_slots'], 2)
        self.assertIsNone(v1['coverage'])

        # 晚到首测补入原批 → 另发一版
        c.post('/acceptance_batches/register-reading', headers=operator, json={
            'batch': 'B2', 'site_key': 'PANEL-A:40:40', 'reading_kind': '首测',
            'delta_e': 3.5, 'receipt': 'M-2'})
        v2 = c.post('/acceptance_batches/reissue-batch',
                    json={'batch': 'B2'}, headers=reviewer).json()
        self.assertEqual(v2['version_no'], 2)
        self.assertEqual(v2['measured_slots'], 1)
        self.assertEqual(v2['missing_slots'], 1)
        self.assertEqual(v2['coverage'], 1.0)

        # 批次列表显示当前版次
        batches = c.get('/acceptance_batches', headers=viewer).json()
        b2 = next(b for b in batches if b['code'] == 'B2')
        self.assertEqual(b2['current_version'], 2)
        self.assertEqual(b2['state'], '已封批')


if __name__ == '__main__':
    unittest.main(verbosity=2)
