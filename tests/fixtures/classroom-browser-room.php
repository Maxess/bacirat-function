<?php
declare(strict_types=1);

// Render the production template with test-only endpoints and synthetic identities.
$plugin = getenv('MPM_TEST_CLASSROOM_ROOT');
require $plugin . '/lesson.php';
$manifest = json_decode(file_get_contents($plugin . '/plugin.json'), true);
$GLOBALS['MTAG_PLUGIN_CONTEXT'] = ['base'=>'/plugins/school-plugins.php?plugin=school-classroom', 'site_base'=>'/', 'csrf'=>'fixture', 'store'=>'fixture',
    'asset_hashes'=>$manifest['files'], 'pdf_hash'=>$manifest['files'][$manifest['pdf_resources']]];
$actor = ['role'=>getenv('MPM_TEST_ROOM_ROLE') ?: 'teacher'];
$staff = cl_is_staff($actor);
$cfg = ['room_video_quality'=>true,'screen_share'=>true,'student_screen_share'=>true];
$class = ['id'=>22];
$schoolName = 'مدرسهٔ آزمایشی';
$classLabel = '۷/۱';
$pageTitle = $schoolName . ' ' . $classLabel;
$panel = 'school-panel.php';
$bellDate = '2026-11-18';
$choices = [];
?>
<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="<?= cl_html(cl_asset('classroom.css')) ?>">
<script defer src="<?= cl_html(cl_asset('board.js')) ?>"></script>
<script defer src="/fixture.js"></script>
<?php if (getenv('MPM_TEST_ROOM_LIVE') === 'yes'): ?>
<script defer src="<?= cl_html(cl_asset('room-attention.js')) ?>"></script>
<script defer src="<?= cl_html(cl_asset('classroom.js')) ?>"></script>
<?php endif; ?>
</head><body><main class="school-main room-main">
<?php require $plugin . '/room-view.php'; ?>
</main></body></html>
