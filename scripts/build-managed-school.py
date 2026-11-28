#!/usr/bin/env python3
"""Build the managed classroom site release or reproduce the separate VPS archives."""
import argparse
import ast
import base64
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import tarfile
from urllib.request import urlopen
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts'
HOST = ROOT / 'plugin-manager'
SRC = ROOT / 'school-addon/upload/online-classroom'
LEGACY = ARTIFACTS / 'school-classroom-pilot-v0.1.0.zip'
LEGACY_SHA = 'bebc35a02f57346880373a034ee493b757a8017a12812a133ab957358d9de161'
VERSION = '0.4.28'
MANAGER_VERSION = '0.1.35'
# The historical pairing companion through VPS 1.3.2 stays byte-for-byte frozen.
PAIRING_CLASSROOM_VERSION = '0.4.24'
PAIRING_MANAGER_VERSION = '0.1.31'
PAIRING_MANAGER_SHA = 'bc7f98340b56b23cdf540a9175b63a5d8ab070ed551cff680b2f6439842a84b0'
FULL_MANAGER_BASE = 'baci-plugin-manager-v0.1.30.zip'
FULL_MANAGER_BASE_SHA = '4d51ff9cdca5f71b7348c36ba08c2c8cab787a496e3583c2f97ddd33c76b8982'
# Reuse byte-verified SDK/PDF assets; ship the same board implementation as the site.
RECORDER_BASE = 'baci-classroom-recorder-v0.4.21.zip'
RECORDER_BASE_SHA = 'ca64a9057dfbb758502f5ca89850fb9256f4a4c30266a7f8dd09864d08612cfe'
RECORDER_VERSION = '0.4.23'
RECORDER_SRC = ROOT / 'server/recorder'
FROZEN_RECORDER = ARTIFACTS / f'baci-classroom-recorder-v{RECORDER_VERSION}.zip'
FROZEN_RECORDER_SHA = '663da68a85bdfac47ae2a0fbb48628d856f567e116bf367bdbd28ff935b490d1'
RENDERER_FIX = ('movie.py', 'postprocess.py')
RENDERER_REPAIR = f'baci-classroom-renderer-v{RECORDER_VERSION}-r1.zip'
RENDERER_INSTALLER = f'repair-renderer-v{RECORDER_VERSION}-r1.py'
AUDIO_REPAIR = f'baci-recorder-audio-repair-v{RECORDER_VERSION}-r2.py'
AUDIO_REPAIR_SHA = '88776bf3a247f7bab482b33931c281d1bb3638e1b2981bc8738c6c5a8cb5df73'
R1_SHA = '07d51104f5fa7bbfd90d6329931fa0e9d03967b9c8fc617c8fe08f10b4d38cfd'
R3_RECORDER = f'baci-classroom-recorder-v{RECORDER_VERSION}-r3.zip'
R3_RECORDER_SHA = '7f5863dd842fbcc3298d0605aa745b517a7e0298baae0c471215ab4df407cd26'
FRESH_RECORDER = f'baci-classroom-recorder-v{RECORDER_VERSION}-r4.zip'
MAINTENANCE_SHA = '2782b003fb54886777e2ea3e06b76d9587d1b3ef89bcc0e736e15283ca5782f6'
# Go 1.26.2, linux/amd64, same bytes as the verified GitHub Actions build.
NATIVE = {
    'baci-native-receiver': 'f142af48da82dcbe7dfc2bb233336c5e22a5c1a2be87e2d383b15dd6819a76ce',
    'baci-native-NOTICES.txt': '868dad183677e4aa829cf45f531ff5e91fa9a2a77a7f8a2a36af8dc01bb965c4',
}
# Vazirmatn v33.003, byte-identical to the live classroom webfonts (see fonts/README.md).
RECORDER_FONTS = {
    'fonts/Vazirmatn-Regular.woff2': 'e382101336c6eb32cfb31381c027d02d2e0354bad08f6a395d4088beb3db3d91',
    'fonts/Vazirmatn-Bold.woff2': '836fae7d42d83faa249bc00e0099592be98a1fa260d22d82f269b6091e585627',
    'fonts/OFL.txt': 'e6f4ce7b6c830d5a25a4f8ac777c267cd7d3c180bc2a7150ca57cbb9b280c4b7',
}
BACI = HOST / 'baci'
BACI_PATCH = 'baci-4.221.0-e0f3f686.patch'
BACI_FILES = ('other-settings.php', 'includes/header.php', 'includes/footer.php', 'includes/management_hub.php',
              'includes/classroom-school-bots.php', 'school-classroom-settings.php', 'includes/student_nav.php', 'includes/teacher_nav.php')
PDF_VERSION = '6.5.438'
PDF_SHA = 'be0f0fc35c7e0052b2e00303be1b9697970e615f1debdf14a0400da0516173bf61a970e7650aa7397a31c371c6f74ed61ce8352583af2e13fbe7a6e875d171a4'
PDF_URL = f'https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-{PDF_VERSION}.tgz'


def write_zip(path, entries):
    with ZipFile(path, 'w') as archive:
        for name, data in sorted(entries.items()):
            item = ZipInfo(name)
            item.external_attr = (0o100755 if name == 'baci-native-receiver' else 0o100644) << 16
            archive.writestr(item, data, compress_type=ZIP_DEFLATED, compresslevel=9)


def pdf_entries(archive_path):
    if archive_path:
        raw = archive_path.read_bytes()
    else:
        with urlopen(PDF_URL, timeout=30) as response:
            raw = response.read(11 * 1024 * 1024)
    if hashlib.sha512(raw).hexdigest() != PDF_SHA:
        raise ValueError('PDF.js upstream archive does not match the reviewed SHA-512')
    with tarfile.open(fileobj=BytesIO(raw), mode='r:gz') as tar:
        members = {m.name[8:]: m for m in tar.getmembers() if m.isfile() and m.name.startswith('package/')}
        def read(name):
            with tar.extractfile(members[name]) as f:
                return f.read()
        runtime = {f'runtime/{n}': read(f'legacy/build/{n}') for n in ('pdf.min.mjs','pdf.worker.min.mjs')}
        # A small, checked change on top of the pinned upstream: only fonts not embedded in the PDF
        # get the site's Arabic-script fallback. Preserve embedded fonts, glyph positions and advances.
        main = 'runtime/pdf.min.mjs'
        original = b'a=n.systemFontInfo?.css||`"${s}", ${n.fallbackName}`;'
        fallback = b'a=(n.missingFile?\'"ClassroomPDFPersian", \' : "")+(n.systemFontInfo?.css||`"${s}", ${n.fallbackName}`);'
        if runtime[main].count(original) != 1:
            raise ValueError('Review the PDF.js missing-font fallback for this upstream version')
        runtime[main] = (b'/* Baci: add a local Arabic-script fallback for unembedded fonts. */\n'
                         + runtime[main].replace(original, fallback, 1))
        selected = {n: read(n) for n in members if re.fullmatch(
            r'(?:cmaps|standard_fonts|iccs|wasm)/(?:[A-Za-z0-9_.-]+\.(?:bcmap|ttf|pfb|icc)|(?:jbig2|openjpeg|qcms_bg)\.wasm|(?:jbig2|openjpeg)_nowasm_fallback\.js|LICENSE[A-Z0-9_]*)', n)}
        data = BytesIO()
        # Keep the reviewed PDF runtime and resources in one verified ZIP. The manager
        # serves its deflated members directly as gzip, without storing duplicate variants.
        write_zip(data, selected | runtime)
        return {'assets/pdf-resources.zip': data.getvalue(), 'assets/pdf.LICENSE.txt': read('LICENSE')}


def video_settings(presets):
    for kind in ('camera', 'screen'):
        group = presets[kind]
        for level, label in presets['levels'].items():
            size, fps = group['defaults'][level]
            title = f"{group['label']} — {label}"
            yield {'key': f'{kind}_{level}_size', 'type': 'select', 'label': f'{title} · وضوح',
                   'default': size, 'options': group['sizes']}
            yield {'key': f'{kind}_{level}_fps', 'type': 'select', 'label': f'{title} · فریم بر ثانیه',
                   'default': str(fps), 'options': [str(n) for n in range(presets['fps']['min'], presets['fps']['max'] + 1)]}


def default_video_settings(presets):
    for role, audience in (('teacher', 'دبیر'), ('student', 'دانش‌آموز')):
        for kind in ('camera', 'screen'):
            yield {'key': f'default_{role}_{kind}', 'type': 'select',
                   'label': f'{presets[kind]["label"]} {audience}', 'default': 'low',
                   'group': 'کیفیت پیش‌فرض کلاس‌ها',
                   'options': list(presets['levels']), 'option_labels': presets['levels']}


def plugin_entries(pdf_archive):
    files = {name: (SRC / name).read_bytes() for name in (
        'index.php', 'core.php', 'calendar.php', 'api.php', 'deactivate.php', 'media.php', 'school.php', 'store.php', 'lesson.php',
        'activity.php', 'content.php', 'events.php', 'reports.php', 'notifications.php', 'job.php', 'room-view.php',
        'quality.php', 'video-presets.json', 'recording.php', 'recording-api.php', 'recordings-view.php',
        'assets/classroom.js', 'assets/classroom.css', 'assets/board.js', 'assets/room-attention.js',
    )}
    if hashlib.sha256(LEGACY.read_bytes()).hexdigest() != LEGACY_SHA:
        raise ValueError('Original reviewed SDK archive is not the published v0.1.0 ZIP')
    with ZipFile(LEGACY) as archive:
        for name in ('assets/livekit-client.umd.js', 'assets/livekit-client.LICENSE.txt'):
            files[name] = archive.read(f'upload/online-classroom/{name}')
    files.update(pdf_entries(pdf_archive))
    presets = json.loads(files['video-presets.json'])
    manifest = {
        'format': 1, 'id': 'school-classroom', 'version': VERSION, 'title': 'کلاس مجازی',
        'menus': {role: 'کلاس مجازی' for role in ('student', 'teacher', 'admin', 'deputy')},
        'report_menus': {role: 'حضور و غیاب کلاس مجازی' for role in ('teacher', 'admin', 'deputy')},
        'routes': {'page': 'index.php', 'api': 'api.php', 'content': 'content.php', 'events': 'events.php', 'job': 'job.php', 'recording': 'recording-api.php'},
        'store': True, 'pdf_resources': 'assets/pdf-resources.zip',
        'preserve_settings_from': ['0.2.0', '0.2.1', '0.2.2', '0.2.3', '0.3.0', '0.4.0', '0.4.1', '0.4.2', '0.4.3', '0.4.4', '0.4.5', '0.4.6', '0.4.7', '0.4.8', '0.4.9', '0.4.10', '0.4.11', '0.4.12', '0.4.13', '0.4.14', '0.4.15', '0.4.16', '0.4.17', '0.4.18', '0.4.19', '0.4.20', '0.4.21', '0.4.22', '0.4.23', '0.4.24', '0.4.25', '0.4.26', '0.4.27'],
        'hooks': {'deactivate': 'deactivate.php'},
        'settings': [
            {'key': 'signal_url', 'type': 'wss', 'label': 'آدرس رسانه (wss://)'},
            {'key': 'api_key', 'type': 'token', 'label': 'شناسهٔ رسانه (API Key)', 'min': 12, 'max': 64},
            {'key': 'api_secret', 'type': 'secret', 'label': 'کلید خصوصی رسانه (API Secret)', 'min': 32, 'max': 128},
            {'key': 'max_participants', 'type': 'integer', 'min': 4, 'max': 60, 'default': 4, 'label': 'سقف اتصال هر اتاق، شامل دبیر/مسئول (پایلوت: ۴)'},
            {'key': 'screen_share', 'type': 'switch', 'label': 'اشتراک سبک صفحه (بدون صدای سیستم)'},
            {'key': 'student_screen_share', 'type': 'switch', 'label': 'اشتراک صفحهٔ دانش‌آموز (با اجازهٔ موردی دبیر؛ نیازمند اشتراک صفحه)'},
            {'key': 'camera_share', 'type': 'switch', 'label': 'وبکم کم‌مصرف (تصویر مربع، با اجازهٔ دبیر)'},
            {'key': 'room_video_quality', 'type': 'switch', 'label': 'اجازهٔ تغییر کیفیت تصویر در اتاق هر کلاس (دبیر یا مسئول مجاز)'},
            {'key': 'class_media_modes', 'type': 'class-modes', 'label': 'حالت رسانه برای هر کلاس'},
            *default_video_settings(presets),
            *video_settings(presets),
            {'key': 'record_lessons', 'type': 'switch', 'label': 'ضبط اختیاری کلاس با سرویس جداگانهٔ VPS'},
            {'key': 'recording_delay_minutes', 'type': 'integer', 'min': 1, 'max': 720, 'default': 180,
             'label': 'فاصلهٔ پایان واقعی زنگ تا امکان شروع ساخت فیلم (دقیقه؛ پیش‌فرض: ۱۸۰)'},
            {'key': 'recording_secret', 'type': 'secret', 'label': 'کلید مستقل سرویس ضبط (فقط برای ضبط؛ متفاوت از کلید رسانه)', 'min': 32, 'max': 128},
            {'key': 'repeat_bells', 'type': 'switch', 'label': 'اجازهٔ برگزاری مجدد زنگ پایان‌یافته در همان روز (دستی، با گزارش جدا)'},
            {'key': 'attendance_min_minutes', 'type': 'integer', 'min': 0, 'max': 120, 'default': 0,
             'label': 'حداقل حضور برای اعلان (دقیقه؛ صفر: بدون حداقل)'},
            {'key': 'notify_school_bale', 'type': 'switch', 'label': 'ارسال حضور به گفت‌وگوهای خصوصی ربات بله'},
            {'key': 'notify_school_telegram', 'type': 'switch', 'label': 'ارسال حضور به گفت‌وگوهای خصوصی ربات تلگرام'},
            {'key': 'notify_school_rubika', 'type': 'switch', 'label': 'ارسال حضور به گفت‌وگوهای خصوصی ربات روبیکا'},
            {'key': 'bell_hours', 'type': 'bell-hours', 'label': 'ساعت زنگ‌های کلاس مجازی'},
            {'key': 'friday_test', 'type': 'friday-test', 'label': 'آزمایش ۲۴ ساعتهٔ جمعه'},
        ],
        'files': {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    files['plugin.json'] = (json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()
    return files


def frozen_recorder():
    raw = FROZEN_RECORDER.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FROZEN_RECORDER_SHA:
        raise ValueError('Expected the unchanged published 0.4.23 Recorder ZIP')
    return ZipFile(BytesIO(raw))


def renderer_candidate(output_dir):
    # Test bundle, deliberately not named/pinned as the production 0.4.23 payload.
    with frozen_recorder() as base:
        entries = {name: base.read(name) for name in base.namelist()}
    entries.update({name: (RECORDER_SRC / name).read_bytes() for name in (*RENDERER_FIX, 'frame.js', 'replay.js')})
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f'baci-classroom-recorder-v{RECORDER_VERSION}-renderer-candidate.zip'
    write_zip(path, entries)
    print(f'{path}: {hashlib.sha256(path.read_bytes()).hexdigest()}')
    return path


def renderer_repair_entries():
    with frozen_recorder() as base:
        inventory = {name: hashlib.sha256(base.read(name)).hexdigest() for name in base.namelist()}
    maintenance = (RECORDER_SRC / f'upgrade-v{RECORDER_VERSION}.py').read_bytes()
    if hashlib.sha256(maintenance).hexdigest() != MAINTENANCE_SHA:
        raise ValueError('Renderer repair requires the already-reviewed 0.4.23 maintenance guards')
    return {'inventory.json': json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode('ascii'),
            'maintenance.py': maintenance} | {name: (RECORDER_SRC / name).read_bytes() for name in RENDERER_FIX}


def renderer_repair(output_dir):
    data = BytesIO()
    write_zip(data, renderer_repair_entries())
    digest = hashlib.sha256(data.getbuffer()).hexdigest()
    installer = (RECORDER_SRC / RENDERER_INSTALLER).read_bytes()
    if f"PATCH_SHA256 = '{digest}'\n".encode('ascii') not in installer:
        raise ValueError('Renderer repair archive differs from the reviewed r1 installer pin')
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / RENDERER_REPAIR
    path.write_bytes(data.getbuffer())
    print(f'{path}: {digest}')
    return path


def audio_repair(output_dir):
    template = (RECORDER_SRC / f'repair-audio-v{RECORDER_VERSION}-r2.py').read_bytes()
    with frozen_recorder() as base:
        stock = base.read('replay.js')
    # The independent 1.3.2 repair stays pinned after the fresh renderer evolves.
    tree = ast.parse(verified_release(AUDIO_REPAIR, AUDIO_REPAIR_SHA))
    [fixed] = [item.value for item in tree.body if isinstance(item, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == 'FIXED' for target in item.targets)]
    reviewed = base64.b64decode(ast.literal_eval(fixed), validate=True)
    for label, data in (('STOCK', stock), ('AFTER', reviewed)):
        pin = hashlib.sha256(data).hexdigest()
        if f"{label}_SHA = '{pin}'".encode('ascii') not in template:
            raise ValueError('Audio repair only builds with the reviewed replay bytes')
    for key, data in ((b'@@STOCK_REPLAY@@', stock), (b'@@FIXED_REPLAY@@', reviewed)):
        if template.count(key) != 1:
            raise ValueError('Unexpected audio repair template')
        # Wrap one adjacent bytes literal per line so both source and artifact
        # remain readable without imposing a zlib version on reproducibility.
        content = base64.b64encode(data)
        literal = b'(\n' + b''.join(b"    b'" + content[i:i + 100] + b"'\n" for i in range(0, len(content), 100)) + b')'
        template = template.replace(b"b'" + key + b"'", literal)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / AUDIO_REPAIR
    path.write_bytes(template)
    print(f'{path}: {hashlib.sha256(template).hexdigest()}')
    return path


def recorder_release(output_dir, native_build):
    previous = (ARTIFACTS / RECORDER_BASE).read_bytes()
    if hashlib.sha256(previous).hexdigest() != RECORDER_BASE_SHA:
        raise ValueError('Expected the unchanged published Recorder base ZIP')
    with ZipFile(BytesIO(previous)) as archive:
        assets = {name: archive.read(name) for name in ('livekit-client.umd.js', 'livekit-client.LICENSE.txt',
                'pdf-resources.zip', 'pdf.LICENSE.txt')}
    assets['board.js'] = (SRC / 'assets/board.js').read_bytes()
    files = {name: (RECORDER_SRC / name).read_bytes() for name in ('requirements.txt',
             'capture.py', 'view.html', 'view.css', 'view.js',
             'archive.py', 'clock.py', 'disk_writer.py', 'native_capture.py', 'ingest.html', 'ingest.js', 'encoded.js',
             'school-classroom-recorder.service', 'school-classroom-postprocess.service',
             'school-classroom-recorder.service.d/10-recorder-credential.conf',
             'school-classroom-recorder.service.d/60-native-recorder.conf', 'fonts/README.md')}
    # Renderer candidates and HTTP repairs never change the published 0.4.23 ZIP.
    with frozen_recorder() as published:
        files.update({name: published.read(name) for name in (*RENDERER_FIX, 'README-fa.md', 'replay.js', 'frame.js', 'recording.py', 'transport.py')})
    for name, digest in NATIVE.items():
        data = (native_build / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f'Native build output differs from the pinned bytes: {name}')
        files[name] = data
    for name, digest in RECORDER_FONTS.items():
        data = (RECORDER_SRC / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f'Expected the reviewed Recorder font/license: {name}')
        files[name] = data
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / FROZEN_RECORDER.name
    data = BytesIO()
    write_zip(data, files | assets)
    digest = hashlib.sha256(data.getbuffer()).hexdigest()
    if digest != FROZEN_RECORDER_SHA:
        raise ValueError('Rebuilt Recorder is not byte-identical to the published 0.4.23 ZIP')
    path.write_bytes(data.getbuffer())
    print(f'{path}: {digest}')
    return path


def recorder_fresh(output_dir):
    """Keep the reviewed r3 renderer, media receiver and HTTP client unchanged."""
    with ZipFile(BytesIO(verified_release(R3_RECORDER, R3_RECORDER_SHA))) as r3:
        files = {name: r3.read(name) for name in r3.namelist()}
    files['recording.py'] = (RECORDER_SRC / 'recording.py').read_bytes()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / FRESH_RECORDER
    write_zip(path, files)
    print(f'{path}: {hashlib.sha256(path.read_bytes()).hexdigest()}')
    return path


def checksums(items):
    return ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name, data in sorted(items)).encode('ascii')


def verified_release(name, digest):
    raw = (ARTIFACTS / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f'Expected the unchanged published archive: {name}')
    return raw


def pairing_manager(output_dir):
    upgrade = f'baci-plugin-manager-update-v{PAIRING_MANAGER_VERSION}.zip'
    updater = verified_release(upgrade, PAIRING_MANAGER_SHA)
    classroom = f'school-classroom-plugin-v{PAIRING_CLASSROOM_VERSION}.zip'
    plugin = (ARTIFACTS / classroom).read_bytes()
    with ZipFile(BytesIO(updater)) as manager, ZipFile(BytesIO(plugin)) as archive:
        pin = hashlib.sha256(archive.read('plugin.json')).hexdigest()
        if f"'{PAIRING_CLASSROOM_VERSION}' => '{pin}'".encode() not in manager.read('plugins/school-plugins.php'):
            raise ValueError('Frozen pairing manager must trust the published classroom ZIP')
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / upgrade).write_bytes(updater)
    companion = {upgrade: updater, classroom: plugin,
                 'START-HERE-fa.txt': (ROOT / 'server/offline/SITE-PAIRING-fa.txt').read_bytes()}
    write_zip(output_dir / f'baci-classroom-site-pairing-v{PAIRING_MANAGER_VERSION}.zip',
              companion | {'SHA256SUMS.txt': checksums(companion.items())})


def site_managers(output_dir, gateway):
    if f"const MPM_VERSION = '{MANAGER_VERSION}';".encode() not in gateway:
        raise ValueError('Site Manager source and package versions differ')
    archives = (
        (f'baci-plugin-manager-update-v{PAIRING_MANAGER_VERSION}.zip', PAIRING_MANAGER_SHA, 'baci-plugin-manager-update'),
        (FULL_MANAGER_BASE, FULL_MANAGER_BASE_SHA, 'baci-plugin-manager'),
    )
    for base, digest, stem in archives:
        with ZipFile(BytesIO(verified_release(base, digest))) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries['plugins/school-plugins.php'] = gateway
        if stem == 'baci-plugin-manager':
            entries['README-fa.md'] = (HOST / 'README-fa.md').read_bytes()
        write_zip(output_dir / f'{stem}-v{MANAGER_VERSION}.zip', entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--recorder-only', action='store_true', help='rebuild the unchanged standalone VPS release')
    mode.add_argument('--recorder-fresh', action='store_true', help='build the r4 Recorder pack for fresh installs and managed upgrades')
    mode.add_argument('--renderer-candidate', action='store_true', help='build an offline test ZIP, not a production upgrade')
    mode.add_argument('--renderer-repair', action='store_true', help='rebuild the pinned renderer-only r1 repair ZIP')
    mode.add_argument('--audio-repair', action='store_true', help='build the standalone renderer-only r2 audio repair for VPS 1.3.2')
    mode.add_argument('--pairing-manager', action='store_true', help='reproduce the frozen 0.1.31 / 0.4.24 VPS companion')
    parser.add_argument('--approve-reviewed-manifest', action='store_true', help='replace the trusted manifest pin after reviewing the payload')
    parser.add_argument('--pdf-archive', type=Path, help='use a local copy of the same pinned PDF.js npm archive')
    parser.add_argument('--native-build', type=Path, help='output of scripts/build-recorder-native.py (Linux x86-64)')
    opts = parser.parse_args()
    if opts.renderer_candidate or opts.renderer_repair or opts.audio_repair or opts.recorder_fresh:
        if not opts.output_dir or opts.approve_reviewed_manifest or opts.pdf_archive or opts.native_build:
            parser.error('renderer-only builds need an explicit --output-dir and no release build options')
        build = (audio_repair if opts.audio_repair else recorder_fresh if opts.recorder_fresh
                 else renderer_repair if opts.renderer_repair else renderer_candidate)
        build(opts.output_dir)
        return
    opts.output_dir = opts.output_dir or ARTIFACTS
    if opts.pairing_manager:
        if opts.approve_reviewed_manifest or opts.pdf_archive or opts.native_build:
            parser.error('the pairing updater does not rebuild the published site or VPS programs')
        pairing_manager(opts.output_dir)
        return
    if opts.recorder_only:
        if not opts.native_build or opts.approve_reviewed_manifest or opts.pdf_archive:
            parser.error('Recorder rebuilds need --native-build and no site build options')
        recorder_release(opts.output_dir, opts.native_build)
        return
    if opts.native_build:
        parser.error('--native-build applies only to --recorder-only; the default build is site-only')
    entries = plugin_entries(opts.pdf_archive)
    expected = hashlib.sha256(entries['plugin.json']).hexdigest()
    manager = HOST / 'plugins/school-plugins.php'
    code = manager.read_text(encoding='utf-8')
    pattern = rf"('{re.escape(VERSION)}' => ')([0-9a-f]{{64}}|CATALOG_SHA256_PLACEHOLDER)(')"
    matches = list(re.finditer(pattern, code))
    if len(matches) != 1:
        raise ValueError('Add one catalog pin for the new version before building')
    match = matches[0]
    if match[2] != expected:
        if not opts.approve_reviewed_manifest:
            raise ValueError('Managed package differs from approved catalog; review before publishing')
        code = code[:match.start(2)] + expected + code[match.end(2):]
        manager.write_text(code, encoding='utf-8')
    opts.output_dir.mkdir(parents=True, exist_ok=True)
    classroom = f'school-classroom-plugin-v{VERSION}.zip'
    upgrade = f'baci-plugin-manager-update-v{MANAGER_VERSION}.zip'
    full_manager = f'baci-plugin-manager-v{MANAGER_VERSION}.zip'
    write_zip(opts.output_dir / classroom, entries)
    site_managers(opts.output_dir, code.encode('utf-8'))
    # Site updates are independent of VPS binaries; new layout controls are negotiated per recording.
    outer = f'baci-classroom-site-upgrade-v{VERSION}.zip'
    site_names = (upgrade, classroom)
    bundle = {name: (opts.output_dir / name).read_bytes() for name in site_names}
    bundle['START-HERE-fa.txt'] = (ROOT / f'docs/baci-classroom-site-v{VERSION}.fa.txt').read_bytes()
    write_zip(opts.output_dir / outer, bundle | {'SHA256SUMS.txt': checksums(bundle.items())})
    (opts.output_dir / f'SHA256SUMS-v{VERSION}.txt').write_bytes(checksums(
        (name, (opts.output_dir / name).read_bytes()) for name in (*site_names, full_manager, outer)))
    print(f'Built site archives; classroom manifest SHA-256: {expected}')


if __name__ == '__main__':
    main()
