#!/usr/bin/env python3
"""Assemble the managed classroom, Baci update, fresh-install overlay and download bundle."""
import argparse
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
VERSION = '0.4.19'
MANAGER_VERSION = '0.1.25'
# Reuse byte-verified SDK/PDF assets; ship the same board implementation as the site.
RECORDER_BASE = 'baci-classroom-recorder-v0.4.17.zip'
RECORDER_BASE_SHA = '6929be4f172b08a88709c1601b4351e7f6ea13574d7299126fcc482b1787dfaa'
RECORDER_VERSION = '0.4.18'
RECORDER_SRC = ROOT / 'server/recorder'
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
            item.external_attr = 0o100644 << 16
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
        'preserve_settings_from': ['0.2.0', '0.2.1', '0.2.2', '0.2.3', '0.3.0', '0.4.0', '0.4.1', '0.4.2', '0.4.3', '0.4.4', '0.4.5', '0.4.6', '0.4.7', '0.4.8', '0.4.9', '0.4.10', '0.4.11', '0.4.12', '0.4.13', '0.4.14', '0.4.15', '0.4.16', '0.4.17', '0.4.18'],
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
            *default_video_settings(presets),
            *video_settings(presets),
            {'key': 'record_lessons', 'type': 'switch', 'label': 'ضبط اختیاری کلاس با سرویس جداگانهٔ VPS'},
            {'key': 'recording_secret', 'type': 'secret', 'label': 'کلید مستقل سرویس ضبط (فقط برای ضبط؛ متفاوت از کلید رسانه)', 'min': 32, 'max': 128},
            {'key': 'repeat_bells', 'type': 'switch', 'label': 'اجازهٔ برگزاری مجدد زنگ پایان‌یافته در همان روز (دستی، با گزارش جدا)'},
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


def recorder_release(output_dir, previous):
    with ZipFile(BytesIO(previous)) as archive:
        assets = {name: archive.read(name) for name in ('livekit-client.umd.js', 'livekit-client.LICENSE.txt',
                'pdf-resources.zip', 'pdf.LICENSE.txt')}
    assets['board.js'] = (SRC / 'assets/board.js').read_bytes()
    files = {name: (RECORDER_SRC / name).read_bytes() for name in ('README-fa.md', 'requirements.txt',
             'capture.py', 'recording.py', 'transport.py', 'view.html', 'view.css', 'view.js', 'frame.js',
             'school-classroom-recorder.service')}
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f'baci-classroom-recorder-v{RECORDER_VERSION}.zip'
    write_zip(path, files | assets)
    print(f'{path}: {hashlib.sha256(path.read_bytes()).hexdigest()}')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ARTIFACTS)
    parser.add_argument('--recorder-only', action='store_true', help='build the standalone VPS release, without changing the site packages')
    parser.add_argument('--approve-reviewed-manifest', action='store_true', help='replace the trusted manifest pin after reviewing the payload')
    parser.add_argument('--pdf-archive', type=Path, help='use a local copy of the same pinned PDF.js npm archive')
    opts = parser.parse_args()
    if opts.recorder_only:
        recorder_base = (ARTIFACTS / RECORDER_BASE).read_bytes()
        if hashlib.sha256(recorder_base).hexdigest() != RECORDER_BASE_SHA:
            raise ValueError('Expected the unchanged published Recorder base ZIP')
        if opts.approve_reviewed_manifest or opts.pdf_archive:
            parser.error('site manifest and PDF build options do not apply to the standalone Recorder')
        recorder_release(opts.output_dir, recorder_base)
        return
    entries = plugin_entries(opts.pdf_archive)
    expected = hashlib.sha256(entries['plugin.json']).hexdigest()
    manager = HOST / 'plugins/school-plugins.php'
    code = manager.read_text(encoding='utf-8')
    if f"const MPM_VERSION = '{MANAGER_VERSION}';" not in code:
        raise ValueError('Manager source and archive versions differ')
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
    # Old mtagbot other-settings.php bypassed the Baci hub. Restore the pinned
    # native entrypoint and footer along with the integrated hub and gateway.
    update = {'plugins/school-plugins.php': manager.read_bytes(),
              'includes/classroom-calendar.php': entries['calendar.php'],
              **{name: (BACI / name).read_bytes() for name in BACI_FILES}}
    upstream = json.loads((BACI / 'UPSTREAM.json').read_text())
    for name in ('other-settings.php', 'includes/footer.php'):
        if hashlib.sha256(update[name]).hexdigest() != upstream['original_files'][name]:
            raise ValueError(f'Expected the original Baci release file: {name}')
    opts.output_dir.mkdir(parents=True, exist_ok=True)
    classroom = f'school-classroom-plugin-v{VERSION}.zip'
    upgrade = f'baci-plugin-manager-update-v{MANAGER_VERSION}.zip'
    write_zip(opts.output_dir / classroom, entries)
    write_zip(opts.output_dir / upgrade, update)
    write_zip(opts.output_dir / f'baci-plugin-manager-v{MANAGER_VERSION}.zip', {
        **update, 'README-fa.md': (HOST / 'README-fa.md').read_bytes(),
        'UPSTREAM.json': (BACI / 'UPSTREAM.json').read_bytes(),
        **{f'media/{name}': (ROOT / 'server' / name).read_bytes()
            for name in ('README-fa.md', 'WEBHOOK-fa.md', 'install.py', 'admission.py')},
        f'patches/{BACI_PATCH}': (HOST / 'patches' / BACI_PATCH).read_bytes(),
    })
    # This hotfix only changes the school site; leave the published VPS release alone.
    bundle = {name: (opts.output_dir / name).read_bytes() for name in (upgrade, classroom)}
    checksums = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name, data in bundle.items())
    write_zip(opts.output_dir / f'baci-classroom-upgrade-v{VERSION}.zip', {
        **bundle, 'START-HERE-fa.txt': (ROOT / 'docs/baci-classroom-upgrade.fa.txt').read_bytes(),
        'SHA256SUMS.txt': checksums.encode('ascii'),
    })
    print(f'Built all archives; classroom manifest SHA-256: {expected}')


if __name__ == '__main__':
    main()
