"""Optional integration check: unmodified Baci V1.15 site archive + disposable MySQL.

Set MPM_TEST_BACI, MPM_TEST_DSN, MPM_TEST_USER and MPM_TEST_PASSWORD as for the
other PHP tests, using a separate, empty fixture DB. MPM_TEST_PHP defaults to
`php` (can be `frankenphp php-cli`). The test creates and drops fixture tables.
"""
import csv
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts'
PIN = json.loads((ROOT / 'plugin-manager/baci/UPSTREAM.json').read_text())
UPDATE = 'baci-plugin-manager-update-v0.1.23.zip'
CLASSROOM = ARTIFACTS / 'school-classroom-plugin-v0.4.17.zip'
SITE_TABS = {
    'logs': ('لاگ فعالیت', 'activity-logs.php'),
    'migration': ('مایگریشن', 'migration-updater.php'),
    'backups': ('پشتیبان‌گیری', 'backups.php'),
    'api': ('مستندات API', 'api/docs.php'),
    'sync': ('همگام‌سازی با سایت', 'desk-sync.php'),
    'health': ('سلامت پایگاه داده', 'db-optimizer.php'),
    'performance': ('کارایی و سرعت سایت', 'performance.php'),
    'queue': ('صف اعلان‌های ربات', 'bot-queue.php'),
    'inbox': ('پیام‌های دریافتی ربات', 'bot-inbox.php'),
    'desktop-updates': ('انتشار به‌روزرسانی دسکتاپ', 'desk-updates.php'),
    'admins': ('مدیریت مدیران', 'admins.php'),
}


class Hub(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.tabs, self.active, self.frame, self.cell, self.label = {}, None, None, None, None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'a' and 'data-hub-key' in a:
            self.label = (a['data-hub-key'], a['data-hub-src'])
            if a['aria-selected'] == 'true':
                self.active = a['data-hub-key']
        if tag == 'iframe' and a.get('id') == 'hubFrame':
            self.frame = a['src']
        if tag == 'td' and a.get('data-day') == 'یک‌شنبه' and a.get('data-period') == 'زنگ 1':
            if a.get('data-class', '').replace(' ', '') == 'هفتم1':
                self.cell = a

    def handle_data(self, data):
        if self.label and data.strip():
            key, src = self.label
            self.tabs[key] = (data.strip(), src.removesuffix('?embedded=1'))

    def handle_endtag(self, tag):
        if tag == 'a':
            self.label = None


class Tiles(HTMLParser):
    def __init__(self, page, prefix):
        super().__init__()
        self.prefix = prefix
        self.grid_depth, self.links = 0, []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'div' and (self.grid_depth or self.prefix + '-grid' in a.get('class', '').split()):
            self.grid_depth += 1
        if tag == 'a' and self.grid_depth and self.prefix + '-tile' in a.get('class', '').split():
            self.links.append(a['href'])

    def handle_endtag(self, tag):
        if self.grid_depth and tag == 'div':
            self.grid_depth -= 1


@unittest.skipUnless(os.environ.get('MPM_TEST_BACI') and os.environ.get('MPM_TEST_DSN'),
                     'requires Baci V1.15 and an empty local mpm_fixture_* MariaDB database')
class BaciNativeTest(unittest.TestCase):
    def setUp(self):
        dsn = re.fullmatch(r'mysql:host=127\.0\.0\.1;port=(\d+);dbname=(mpm_fixture_[a-z0-9_]+);charset=utf8mb4',
                           os.environ['MPM_TEST_DSN'])
        self.assertIsNotNone(dsn, 'A disposable local database is required')
        upstream = Path(os.environ['MPM_TEST_BACI'])
        for name, digest in PIN['original_files'].items():
            self.assertEqual(hashlib.sha256((upstream / name).read_bytes()).hexdigest(), digest)
        manifest = json.loads((upstream / 'RELEASE-MANIFEST.json').read_text())
        self.assertEqual((manifest['site_version'], manifest['platform']), (PIN['site_version'], 'site'))
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='baci-native-')))
        self.root = self.work / 'site'
        shutil.copytree(upstream, self.root)
        plugin = self.work / 'private-plugin'
        with ZipFile(CLASSROOM) as package:
            package.extractall(plugin)
        self.php = shlex.split(os.environ.get('MPM_TEST_PHP', 'php'))
        self.env = dict(os.environ, BACI_FIXTURE_ROOT=str(self.root), BACI_TEST_PLUGIN=str(plugin),
                        BACI_TEST_PACKAGE=str(CLASSROOM), BACI_PRIVATE_DIR=str(self.work / 'installation'))
        (self.root / 'config/installed.lock').touch()
        (self.root / 'config/database.php').write_text('''<?php
return ['driver'=>'mysql','host'=>'127.0.0.1','port'=>%s,'database'=>'%s','charset'=>'utf8mb4',
    'username'=>getenv('MPM_TEST_USER') ?: '', 'password'=>getenv('MPM_TEST_PASSWORD') ?: ''];
''' % dsn.groups())
        self.db = "require 'includes/db.php'; $db=DB::getInstance()->getPdo();\n"
        self.assertEqual(self.run_php(self.db + "echo $db->query('SHOW TABLES')->fetchColumn();"), '')
        self.addCleanup(self.run_php, self.db + '''
            foreach ($db->query('SHOW TABLES')->fetchAll(PDO::FETCH_COLUMN) as $table) {
                $db->exec('SET FOREIGN_KEY_CHECKS=0');
                $db->exec('DROP TABLE `'.str_replace('`','``',$table).'`');
            }
        ''')
        self.run_php(self.db + '''
            $db->exec(file_get_contents('sql/database.sql'));
            $db->exec("INSERT INTO academic_years(id,year_name,is_default) VALUES (4,'1404/1405',1),(5,'1405/1406',0);
                INSERT INTO settings(key_name,key_value) VALUES ('current_academic_year','1405/1406');
                INSERT INTO teachers(id,national_id,full_name,password,academic_year,is_deputy,status) VALUES
                    (11,'1111111111','دبیر الف','fixture','1404/1405',0,1),
                    (12,'2222222222','دبیر ب','fixture','1404/1405',0,1),
                    (13,'3333333333','ناظم تست','fixture','1404/1405',1,1),
                    (14,'4444444444','دبیر غیرفعال','fixture','1404/1405',0,0);
                INSERT INTO students(id,national_id,first_name,last_name,class_name,grade_level,status,academic_year) VALUES
                    (41,'7777777777','آزمایش','دانش‌آموز','هفتم1','هفتم','active','1405/1406');
                INSERT INTO admins(id,username,password,name,role,permissions,status) VALUES
                    (1,'qa-root','fixture','مدیر کل','super_admin','[]',1),
                    (2,'qa-limited','fixture','ناظر','observer','[\\\"view_logs\\\"]',1),
                    (3,'qa-inactive','fixture','غیرفعال','super_admin','[]',0);
                INSERT INTO classes(id,name,grade,academic_year) VALUES
                    (21,'هفتم1','هفتم','1405/1406'),(22,'هفتم1','هفتم','1404/1405');
                INSERT INTO class_schedules(academic_year,class_name,day_of_week,period_num,subject_name,teacher_id) VALUES
                    ('1404/1405','هفتم1','یک‌شنبه','زنگ 1','فقط سال قبل',12);");
        ''')

    def run_php(self, code, *, request=None):
        path = self.work / 'fixture.php'
        path.write_text("<?php\nini_set('display_errors', '0');\nchdir(getenv('BACI_FIXTURE_ROOT'));\n$_SERVER['DOCUMENT_ROOT'] = getcwd();\n" + code)
        env = self.env if request is None else dict(self.env, BACI_REQUEST=json.dumps(request))
        result = subprocess.run(self.php + [str(path)], env=env, capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-1800:])
        self.assertNotRegex(result.stderr + result.stdout, r'(?:Fatal|Parse) error:')
        return result.stdout.strip()

    def page(self, path, query=None, post=None, session=None):
        return self.run_php('''
            $req=json_decode(getenv('BACI_REQUEST'), true, 32, JSON_THROW_ON_ERROR);
            $_SERVER['REQUEST_METHOD'] = $req['post'] === null ? 'GET' : 'POST';
            $_SERVER['PHP_SELF'] = $_SERVER['SCRIPT_NAME'] = '/'.$req['path'];
            $_SERVER['SCRIPT_FILENAME'] = getcwd().'/'.$req['path'];
            $_SERVER['DOCUMENT_ROOT']=getcwd();
            $_SERVER['HTTPS']='on'; $_SERVER['HTTP_HOST']='school.fixture.test';
            session_start(); $_SESSION = $req['session'] + ['csrf_token'=>'fixture-csrf'];
            $_GET=$req['query'] ?? []; $_POST=$req['post'] ?? [];
            if ($req['path'] === 'plugins/school-plugins.php') {
                // Invoke the HTTP controller instead of the direct CLI job entry point.
                $_SERVER['SCRIPT_FILENAME']=__FILE__;
                require $req['path'];
                mpm_run();
            } else require $req['path'];
        ''', request={'path': path, 'query': query, 'post': post,
                      'session': session if session is not None else {'admin_id': 1, 'admin_role': 'super_admin'}})

    def install_files(self, archive):
        with ZipFile(ARTIFACTS / archive) as package:
            for name in package.namelist():
                if name.endswith('.php'):
                    dest = self.root / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(package.read(name))

    def test_restore_native_hub_and_read_its_schedule(self):
        self.assertEqual(Hub(self.page('other-settings.php')).tabs, SITE_TABS)
        self.install_files('baci-plugin-manager-update-v0.1.14.zip')
        # Reproduce the legacy controller surviving a gateway-only upgrade.
        for name in ('other-settings.php', 'includes/header.php', 'includes/footer.php'):
            shutil.copyfile(ROOT / 'plugin-manager/mtagbot' / name, self.root / name)
        old = self.page('other-settings.php')
        self.assertEqual(len(re.findall(r'data-hub-url=', old)), 4)
        self.assertIn('other-settings.php?tab=plugins', old)
        self.install_files(UPDATE)
        all_tabs = SITE_TABS | {'plugins': ('افزونه‌ها', 'school-classroom-settings.php')}
        for key, (_, path) in all_tabs.items():
            hub = Hub(self.page('other-settings.php', {'hub_tab': key}))
            self.assertEqual(hub.tabs, all_tabs)
            self.assertEqual((hub.active, hub.frame), (key, path + '?embedded=1'))
            self.assertTrue((self.root / path).is_file())
        limited = {'admin_id': 2, 'admin_role': 'observer'}
        hub = Hub(self.page('other-settings.php', {'hub_tab': 'plugins'}, session=limited))
        self.assertEqual(hub.tabs, {key: SITE_TABS[key] for key in ('logs', 'api')})
        self.assertEqual(hub.active, 'logs')
        self.assertIn('مدیر کل فعال', self.page('school-classroom-settings.php', {'embedded': '1'}, session=limited))
        self.assertNotIn('افزونه‌ها', self.page('other-settings.php', session={'teacher_id': 11}))
        inactive = Hub(self.page('other-settings.php', session={'admin_id': 3, 'admin_role': 'super_admin'}))
        self.assertFalse(set(inactive.tabs) & {'plugins', 'sync', 'desktop-updates'})

        # Use the actual CSV producer, then edit the same cell using the native matrix POST.
        self.assertEqual(Hub(self.page('courses-management.php', {'hub_tab': 'classes'})).frame, 'classes.php?embedded=1')
        uploads = self.root / 'uploads'
        uploads.mkdir(exist_ok=True)
        with (uploads / 'fixture.csv').open('w', newline='') as f:
            subjects, teachers = [''] * 29, [''] * 29
            subjects[28], subjects[22], teachers[22] = 'هفتم 1', 'علوم/هنر', 'دبیر الف/دبیر ب'
            subjects[21], teachers[21] = 'درس بدون دبیر متصل', 'نام بدون حساب'
            csv.writer(f).writerows([[], [], [], subjects, teachers, []])
        self.page('import-schedule.php', post={'action': 'execute_schedule', 'academic_year': '1405/1406', 'csrf_token': 'fixture-csrf'},
                  session={'admin_id': 1, 'admin_role': 'super_admin', 'import_schedule_file': 'fixture.csv'})
        query = {'tab': 'schedule', 'embedded': '1'}
        cell = Hub(self.page('classes.php', query)).cell
        self.assertIsNotNone(cell, 'schedule rendered from the native current year, not the old default flag')
        self.assertEqual((cell['data-s1'], cell['data-s2']), ('علوم', 'هنر'))
        ajax = self.page('classes.php', query, {'ajax_cell_save': '1', 'csrf_token': 'fixture-csrf',
                            'class_name': cell['data-class'], 'day_of_week': 'یک‌شنبه', 'period_num': 'زنگ 1',
                            'subject_name': 'ریاضی', 'teacher_id': '11', 'is_half': '1', 'subject_name2': 'هنر',
                            'teacher_id2': '12', 'revision': cell['data-revision']})
        self.assertTrue(ajax.startswith('{'), ajax[:600])
        result = json.loads(ajax)
        self.assertTrue(result['ok'], result)
        self.assertEqual(Hub(self.page('classes.php', query)).cell['data-s1'], 'ریاضی')

        resolved = json.loads(self.run_php(self.db + '''
            require getenv('BACI_TEST_PLUGIN').'/lesson.php';
            $y=cl_current_year($db); $c=cl_classes($db,$y)[21]; $t=strtotime('2026-11-22T05:00:00Z');
            $slots=cl_slots_for_date($db,$c,$t); $teacher=cl_school_user($db,['teacher_id'=>11],$y);
            $deputy=cl_school_user($db,['teacher_id'=>13],$y);
            echo json_encode([$y,array_values($slots),array_values(cl_today_slots($db,$c,$teacher,$t)),
                array_values(cl_today_slots($db,$c,$deputy,$t)),cl_access_class($db,$c,$teacher),cl_slots_for_date($db,$c,$t-86400)]);
        '''))
        year, slots, teacher_slots, deputy_slots, access, yesterday = resolved
        self.assertEqual(year, '1405/1406')
        self.assertEqual([(s['subject'], s['teacher'], s['shared']) for s in slots], [('ریاضی', 11, True), ('هنر', 12, True)])
        self.assertEqual((teacher_slots, deputy_slots, access, yesterday), ([slots[0]], slots, True, []))


    def test_classroom_tile_in_each_native_grid_only_when_enabled(self):
        self.install_files(UPDATE)
        panels = [('teacher-panel.php', {'teacher_id': 11}, 'tp', 'online-exams.php'),
                  ('student-panel.php', {'student_id': 41}, 'sn', 'student-online-exams.php')]
        url = 'plugins/school-plugins.php?plugin=school-classroom'

        def links():
            for path, account, prefix, existing in panels:
                page = self.page(path, session=account)
                grid = Tiles(page, prefix).links
                self.assertIn(existing, grid, 'native grid still contains its existing links')
                yield grid, page

        for grid, _ in links():
            self.assertNotIn(url, grid)
        self.run_php(self.db + '''
            require 'plugins/school-plugins.php';
            $dir = getenv('BACI_PRIVATE_DIR');
            mpm_initialize($db, 'https://school.fixture.test', $dir);
            mpm_install($db, $dir, getenv('BACI_TEST_PACKAGE'));
        ''')
        for grid, _ in links():
            self.assertNotIn(url, grid)
        self.run_php(self.db + '''
            require 'plugins/school-plugins.php';
            $dir = getenv('BACI_PRIVATE_DIR');
            mpm_write_settings($db, $dir, mpm_row($db, 'school-classroom'),
                ['signal_url'=>'wss://media.fixture.test', 'api_key'=>'local_fixture_key', 'api_secret'=>str_repeat('Q',48)]);
            mpm_query($db, 'UPDATE '. MPM_REGISTRY . ' SET state=? WHERE slug=?', ['enabled', 'school-classroom']);
        ''')
        for grid, page in links():
            self.assertEqual(grid.count(url), 1)
            self.assertIn('>کلاس مجازی<', page)
        self.run_php(self.db + '''
            require getenv('BACI_TEST_PLUGIN').'/school.php';
            $db->prepare("INSERT INTO class_schedules(academic_year,class_name,day_of_week,period_num,subject_name,teacher_id)
                VALUES ('1405/1406','هفتم1',?,'زنگ 1','علوم',11)")->execute([CL_DAYS[(int)cl_day(time())->format('w')]]);
        ''')
        room_query = {'plugin': 'school-classroom', 'class': '21'}
        staff_room = self.page(url.split('?', 1)[0], room_query, session={'teacher_id': 11})
        pupil_room = self.page(url.split('?', 1)[0], room_query, session={'student_id': 41})
        for room in (staff_room, pupil_room):
            self.assertIn('id="classroom"', room)
            self.assertEqual(room.count('id="leave"'), 1)
            self.assertNotIn('id="receive-video"', room)
            self.assertNotIn('id="room-date"', room)
        self.assertRegex(staff_room, r'(?s)id="access-menu".*id="access-features".*class="access-actions".*id="leave".*id="close".*</header>')
        self.assertNotIn('id="close"', pupil_room)
        account = {'teacher_id': 14}  # Host nav must not expose a tile for inactive accounts.
        self.assertNotIn(url, Tiles(self.page('teacher-panel.php', session=account), 'tp').links)
        self.run_php(self.db + '''
            require 'plugins/school-plugins.php';
            mpm_query($db, 'UPDATE '. MPM_REGISTRY . ' SET state=? WHERE slug=?', ['disabled', 'school-classroom']);
        ''')
        for grid, _ in links():
            self.assertNotIn(url, grid)
